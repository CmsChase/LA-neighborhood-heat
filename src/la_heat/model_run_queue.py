"""Durable SQLite task queue for grouped model runs.

The queue is intentionally independent from the model implementation.  A run's
task plan is immutable once initialized, while its desired state can be changed
between ``running`` and ``paused``.  Claims use expiring leases and a monotonically
increasing generation as a fencing token, so a worker that lost a lease cannot
commit a result after another worker has reclaimed the task.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

TASK_STATUSES: Final = ("pending", "running", "complete", "quarantined")
DESIRED_STATES: Final = ("running", "paused")
_SCHEMA_VERSION: Final = 1


class ModelRunQueueError(RuntimeError):
    """Base class for durable model-run queue failures."""


class RunNotFoundError(ModelRunQueueError):
    """Raised when an operation references an unknown run ID."""


class TaskPlanDriftError(ModelRunQueueError):
    """Raised when an existing run ID is initialized with a different task plan."""


class LeaseLostError(ModelRunQueueError):
    """Raised when a worker's owner/generation fencing token is no longer current."""


@dataclass(frozen=True, slots=True)
class TaskSpec:
    """One immutable entry in a model run's task plan."""

    task_id: str
    kind: str
    payload: Any


@dataclass(frozen=True, slots=True)
class TaskRecord:
    """Materialized task state returned by claims and queue inspection."""

    run_id: str
    task_id: str
    kind: str
    payload: Any
    status: str
    attempt: int
    available_at: float
    lease_owner: str | None
    lease_expires_at: float | None
    claim_generation: int
    result: Any | None
    error_type: str | None


def _require_identifier(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string.")
    return value


def _json_text(value: Any, *, label: str) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must be finite JSON data.") from error


def _epoch(value: float | datetime | None) -> float:
    if value is None:
        result = time.time()
    elif isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Datetime timestamps must be timezone-aware.")
        result = value.astimezone(UTC).timestamp()
    else:
        result = float(value)
    if not math.isfinite(result):
        raise ValueError("Timestamp must be finite UTC epoch seconds.")
    return result


def _positive_seconds(value: float, *, label: str) -> float:
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{label} must be finite and greater than zero.")
    return result


def _normalize_spec(value: TaskSpec | Mapping[str, Any]) -> TaskSpec:
    if isinstance(value, TaskSpec):
        spec = value
    elif isinstance(value, Mapping):
        unexpected = set(value) - {"task_id", "kind", "payload"}
        missing = {"task_id", "kind", "payload"} - set(value)
        if unexpected or missing:
            raise ValueError(
                "Task mappings must contain exactly task_id, kind, and payload."
            )
        spec = TaskSpec(
            task_id=value["task_id"],
            kind=value["kind"],
            payload=value["payload"],
        )
    else:
        raise TypeError("Tasks must be TaskSpec instances or task mappings.")
    return TaskSpec(
        task_id=_require_identifier(spec.task_id, label="task_id"),
        kind=_require_identifier(spec.kind, label="kind"),
        payload=spec.payload,
    )


def _normalized_plan(
    tasks: Iterable[TaskSpec | Mapping[str, Any]],
) -> list[tuple[str, str, str]]:
    result: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for value in tasks:
        spec = _normalize_spec(value)
        if spec.task_id in seen:
            raise ValueError(f"Duplicate task_id in plan: {spec.task_id!r}.")
        seen.add(spec.task_id)
        result.append(
            (
                spec.task_id,
                spec.kind,
                _json_text(spec.payload, label=f"payload for {spec.task_id!r}"),
            )
        )
    if not result:
        raise ValueError("A model run must contain at least one task.")
    return result


def _plan_sha256(plan: Sequence[tuple[str, str, str]]) -> str:
    serializable = [
        {"task_id": task_id, "kind": kind, "payload_json": payload_json}
        for task_id, kind, payload_json in plan
    ]
    canonical = _json_text(serializable, label="task plan")
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _decode_optional_json(value: str | None) -> Any | None:
    if value is None:
        return None
    return json.loads(value)


def _row_to_record(row: sqlite3.Row) -> TaskRecord:
    return TaskRecord(
        run_id=str(row["run_id"]),
        task_id=str(row["task_id"]),
        kind=str(row["kind"]),
        payload=json.loads(row["payload_json"]),
        status=str(row["status"]),
        attempt=int(row["attempt"]),
        available_at=float(row["available_at"]),
        lease_owner=row["lease_owner"],
        lease_expires_at=(
            None
            if row["lease_expires_at"] is None
            else float(row["lease_expires_at"])
        ),
        claim_generation=int(row["claim_generation"]),
        result=_decode_optional_json(row["result_json"]),
        error_type=row["error_type"],
    )


class ModelRunQueue:
    """A process-safe, persistent SQLite queue using WAL journal mode."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        busy_timeout_seconds: float = 30.0,
    ) -> None:
        raw_path = str(database_path)
        if raw_path == ":memory:":
            raise ValueError("A persistent model-run queue requires a filesystem path.")
        self.path = Path(database_path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        timeout = _positive_seconds(
            busy_timeout_seconds,
            label="busy_timeout_seconds",
        )
        self._busy_timeout_seconds = timeout
        self._busy_timeout_ms = max(1, round(timeout * 1000))
        self._initialize_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=self._busy_timeout_seconds,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize_schema(self) -> None:
        connection = self._connect()
        try:
            journal_mode = str(connection.execute("PRAGMA journal_mode = WAL").fetchone()[0])
            if journal_mode.lower() != "wal":
                raise ModelRunQueueError("SQLite refused WAL journal mode.")
            connection.execute("PRAGMA synchronous = NORMAL")
            connection.executescript(
                f"""
                CREATE TABLE IF NOT EXISTS model_runs (
                    run_id TEXT PRIMARY KEY,
                    task_plan_sha256 TEXT NOT NULL,
                    desired_state TEXT NOT NULL
                        CHECK (desired_state IN ('running', 'paused')),
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    schema_version INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS model_run_tasks (
                    run_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL
                        CHECK (status IN ('pending', 'running', 'complete', 'quarantined')),
                    attempt INTEGER NOT NULL DEFAULT 0 CHECK (attempt >= 0),
                    available_at REAL NOT NULL,
                    lease_owner TEXT,
                    lease_expires_at REAL,
                    claim_generation INTEGER NOT NULL DEFAULT 0
                        CHECK (claim_generation >= 0),
                    result_json TEXT,
                    error_type TEXT,
                    plan_index INTEGER NOT NULL CHECK (plan_index >= 0),
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (run_id, task_id),
                    UNIQUE (run_id, plan_index),
                    FOREIGN KEY (run_id) REFERENCES model_runs(run_id),
                    CHECK (
                        (status = 'running'
                            AND lease_owner IS NOT NULL
                            AND lease_expires_at IS NOT NULL)
                        OR
                        (status != 'running'
                            AND lease_owner IS NULL
                            AND lease_expires_at IS NULL)
                    )
                );

                CREATE INDEX IF NOT EXISTS model_run_tasks_claim_idx
                ON model_run_tasks(run_id, status, available_at, lease_expires_at, plan_index);

                PRAGMA user_version = {_SCHEMA_VERSION};
                """
            )
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version != _SCHEMA_VERSION:
                raise ModelRunQueueError(
                    f"Unsupported model-run queue schema version: {version}."
                )
        finally:
            connection.close()

    @staticmethod
    def _begin_immediate(connection: sqlite3.Connection) -> None:
        connection.execute("BEGIN IMMEDIATE")

    @staticmethod
    def _commit(connection: sqlite3.Connection) -> None:
        connection.execute("COMMIT")

    @staticmethod
    def _rollback(connection: sqlite3.Connection) -> None:
        if connection.in_transaction:
            connection.execute("ROLLBACK")

    def initialize_run(
        self,
        run_id: str,
        tasks: Iterable[TaskSpec | Mapping[str, Any]],
        *,
        desired_state: str = "running",
        now: float | datetime | None = None,
    ) -> bool:
        """Atomically create a run, or verify its existing task plan exactly.

        Returns ``True`` when a new run was created and ``False`` for an
        idempotent reinitialization.  Reordering, adding, removing, or changing
        any task raises :class:`TaskPlanDriftError`.
        """

        run_id = _require_identifier(run_id, label="run_id")
        if desired_state not in DESIRED_STATES:
            raise ValueError(f"desired_state must be one of {DESIRED_STATES}.")
        plan = _normalized_plan(tasks)
        plan_sha256 = _plan_sha256(plan)
        timestamp = _epoch(now)
        connection = self._connect()
        try:
            self._begin_immediate(connection)
            existing = connection.execute(
                "SELECT task_plan_sha256, schema_version FROM model_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if existing is not None:
                stored_plan = [
                    (str(row["task_id"]), str(row["kind"]), str(row["payload_json"]))
                    for row in connection.execute(
                        """
                        SELECT task_id, kind, payload_json
                        FROM model_run_tasks
                        WHERE run_id = ?
                        ORDER BY plan_index
                        """,
                        (run_id,),
                    )
                ]
                if (
                    int(existing["schema_version"]) != _SCHEMA_VERSION
                    or str(existing["task_plan_sha256"]) != plan_sha256
                    or stored_plan != plan
                ):
                    raise TaskPlanDriftError(
                        f"Task plan drift detected for existing run_id {run_id!r}."
                    )
                self._commit(connection)
                return False

            connection.execute(
                """
                INSERT INTO model_runs(
                    run_id, task_plan_sha256, desired_state,
                    created_at, updated_at, schema_version
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    plan_sha256,
                    desired_state,
                    timestamp,
                    timestamp,
                    _SCHEMA_VERSION,
                ),
            )
            connection.executemany(
                """
                INSERT INTO model_run_tasks(
                    run_id, task_id, kind, payload_json, status, attempt,
                    available_at, lease_owner, lease_expires_at,
                    claim_generation, result_json, error_type, plan_index, updated_at
                ) VALUES (?, ?, ?, ?, 'pending', 0, ?, NULL, NULL, 0, NULL, NULL, ?, ?)
                """,
                [
                    (run_id, task_id, kind, payload_json, timestamp, index, timestamp)
                    for index, (task_id, kind, payload_json) in enumerate(plan)
                ],
            )
            self._commit(connection)
            return True
        except Exception:
            self._rollback(connection)
            raise
        finally:
            connection.close()

    def get_desired_state(self, run_id: str) -> str:
        """Return the persisted desired state for a run."""

        run_id = _require_identifier(run_id, label="run_id")
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT desired_state FROM model_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise RunNotFoundError(f"Unknown run_id {run_id!r}.")
        return str(row["desired_state"])

    def set_desired_state(
        self,
        run_id: str,
        desired_state: str,
        *,
        now: float | datetime | None = None,
    ) -> None:
        """Atomically persist ``running`` or ``paused`` for a run."""

        run_id = _require_identifier(run_id, label="run_id")
        if desired_state not in DESIRED_STATES:
            raise ValueError(f"desired_state must be one of {DESIRED_STATES}.")
        timestamp = _epoch(now)
        connection = self._connect()
        try:
            self._begin_immediate(connection)
            cursor = connection.execute(
                """
                UPDATE model_runs
                SET desired_state = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (desired_state, timestamp, run_id),
            )
            if cursor.rowcount != 1:
                raise RunNotFoundError(f"Unknown run_id {run_id!r}.")
            self._commit(connection)
        except Exception:
            self._rollback(connection)
            raise
        finally:
            connection.close()

    def claim_next(
        self,
        run_id: str,
        *,
        owner: str,
        lease_seconds: float,
        kinds: Iterable[str] | None = None,
        now: float | datetime | None = None,
    ) -> TaskRecord | None:
        """Claim the next due pending task or expired lease under ``BEGIN IMMEDIATE``.

        A paused run returns ``None``.  Each successful claim increments both the
        execution attempt and the fencing generation.
        """

        run_id = _require_identifier(run_id, label="run_id")
        owner = _require_identifier(owner, label="owner")
        lease_seconds = _positive_seconds(lease_seconds, label="lease_seconds")
        selected_kinds: tuple[str, ...] | None = None
        if kinds is not None:
            selected_kinds = tuple(
                dict.fromkeys(
                    _require_identifier(value, label="kind") for value in kinds
                )
            )
            if not selected_kinds:
                raise ValueError("kinds must not be empty when provided.")
        timestamp = _epoch(now)
        lease_expires_at = timestamp + lease_seconds
        connection = self._connect()
        try:
            self._begin_immediate(connection)
            run = connection.execute(
                "SELECT desired_state FROM model_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if run is None:
                raise RunNotFoundError(f"Unknown run_id {run_id!r}.")
            if run["desired_state"] != "running":
                self._commit(connection)
                return None
            kind_clause = ""
            parameters: list[object] = [run_id, timestamp, timestamp]
            if selected_kinds is not None:
                placeholders = ",".join("?" for _ in selected_kinds)
                kind_clause = f" AND kind IN ({placeholders})"
                parameters.extend(selected_kinds)
            candidate = connection.execute(
                f"""
                SELECT task_id
                FROM model_run_tasks
                WHERE run_id = ?
                  AND (
                    (status = 'pending' AND available_at <= ?)
                    OR
                    (status = 'running' AND lease_expires_at <= ?)
                  )
                  {kind_clause}
                ORDER BY plan_index
                LIMIT 1
                """,  # noqa: S608 - placeholders bind every caller value
                parameters,
            ).fetchone()
            if candidate is None:
                self._commit(connection)
                return None
            task_id = str(candidate["task_id"])
            cursor = connection.execute(
                """
                UPDATE model_run_tasks
                SET status = 'running',
                    attempt = attempt + 1,
                    lease_owner = ?,
                    lease_expires_at = ?,
                    claim_generation = claim_generation + 1,
                    result_json = NULL,
                    updated_at = ?
                WHERE run_id = ? AND task_id = ?
                  AND (
                    (status = 'pending' AND available_at <= ?)
                    OR
                    (status = 'running' AND lease_expires_at <= ?)
                  )
                """,
                (
                    owner,
                    lease_expires_at,
                    timestamp,
                    run_id,
                    task_id,
                    timestamp,
                    timestamp,
                ),
            )
            if cursor.rowcount != 1:
                raise ModelRunQueueError("Atomic claim invariant failed.")
            claimed = connection.execute(
                """
                SELECT run_id, task_id, kind, payload_json, status, attempt,
                       available_at, lease_owner, lease_expires_at,
                       claim_generation, result_json, error_type
                FROM model_run_tasks
                WHERE run_id = ? AND task_id = ?
                """,
                (run_id, task_id),
            ).fetchone()
            if claimed is None:
                raise ModelRunQueueError("Claimed task disappeared inside transaction.")
            self._commit(connection)
            return _row_to_record(claimed)
        except Exception:
            self._rollback(connection)
            raise
        finally:
            connection.close()

    def heartbeat(
        self,
        run_id: str,
        task_id: str,
        *,
        owner: str,
        generation: int,
        lease_seconds: float,
        now: float | datetime | None = None,
    ) -> float:
        """Extend a live claim and return its new UTC-epoch expiration time."""

        run_id = _require_identifier(run_id, label="run_id")
        task_id = _require_identifier(task_id, label="task_id")
        owner = _require_identifier(owner, label="owner")
        generation = self._validate_generation(generation)
        lease_seconds = _positive_seconds(lease_seconds, label="lease_seconds")
        timestamp = _epoch(now)
        expires_at = timestamp + lease_seconds
        self._fenced_update(
            run_id,
            task_id,
            owner=owner,
            generation=generation,
            sql="""
                UPDATE model_run_tasks
                SET lease_expires_at = ?, updated_at = ?
                WHERE run_id = ? AND task_id = ? AND status = 'running'
                  AND lease_owner = ? AND claim_generation = ?
            """,
            parameters=(expires_at, timestamp, run_id, task_id, owner, generation),
        )
        return expires_at

    def complete(
        self,
        run_id: str,
        task_id: str,
        *,
        owner: str,
        generation: int,
        result: Any = None,
        now: float | datetime | None = None,
    ) -> None:
        """Complete a claim only if its owner and fencing generation still match."""

        run_id = _require_identifier(run_id, label="run_id")
        task_id = _require_identifier(task_id, label="task_id")
        owner = _require_identifier(owner, label="owner")
        generation = self._validate_generation(generation)
        timestamp = _epoch(now)
        result_json = _json_text(result, label="result")
        self._fenced_update(
            run_id,
            task_id,
            owner=owner,
            generation=generation,
            sql="""
                UPDATE model_run_tasks
                SET status = 'complete', result_json = ?, error_type = NULL,
                    lease_owner = NULL, lease_expires_at = NULL, updated_at = ?
                WHERE run_id = ? AND task_id = ? AND status = 'running'
                  AND lease_owner = ? AND claim_generation = ?
            """,
            parameters=(
                result_json,
                timestamp,
                run_id,
                task_id,
                owner,
                generation,
            ),
        )

    def retry(
        self,
        run_id: str,
        task_id: str,
        *,
        owner: str,
        generation: int,
        error_type: str,
        base_delay_seconds: float,
        max_delay_seconds: float = 3600.0,
        now: float | datetime | None = None,
    ) -> float:
        """Return a claim to pending with attempt-based exponential backoff.

        The first failed attempt waits ``base_delay_seconds``; each subsequent
        failed attempt doubles that delay up to ``max_delay_seconds``.  The
        returned value is the task's next ``available_at`` UTC epoch.
        """

        run_id = _require_identifier(run_id, label="run_id")
        task_id = _require_identifier(task_id, label="task_id")
        owner = _require_identifier(owner, label="owner")
        error_type = _require_identifier(error_type, label="error_type")
        generation = self._validate_generation(generation)
        base_delay = _positive_seconds(
            base_delay_seconds,
            label="base_delay_seconds",
        )
        maximum_delay = _positive_seconds(
            max_delay_seconds,
            label="max_delay_seconds",
        )
        if maximum_delay < base_delay:
            raise ValueError("max_delay_seconds cannot be less than base_delay_seconds.")
        timestamp = _epoch(now)
        connection = self._connect()
        try:
            self._begin_immediate(connection)
            row = connection.execute(
                """
                SELECT attempt
                FROM model_run_tasks
                WHERE run_id = ? AND task_id = ? AND status = 'running'
                  AND lease_owner = ? AND claim_generation = ?
                """,
                (run_id, task_id, owner, generation),
            ).fetchone()
            if row is None:
                raise LeaseLostError(
                    self._lease_lost_message(run_id, task_id, owner, generation)
                )
            exponent = max(0, int(row["attempt"]) - 1)
            delay = min(maximum_delay, base_delay * (2**exponent))
            available_at = timestamp + delay
            cursor = connection.execute(
                """
                UPDATE model_run_tasks
                SET status = 'pending', available_at = ?, error_type = ?,
                    result_json = NULL, lease_owner = NULL,
                    lease_expires_at = NULL, updated_at = ?
                WHERE run_id = ? AND task_id = ? AND status = 'running'
                  AND lease_owner = ? AND claim_generation = ?
                """,
                (
                    available_at,
                    error_type,
                    timestamp,
                    run_id,
                    task_id,
                    owner,
                    generation,
                ),
            )
            if cursor.rowcount != 1:
                raise LeaseLostError(
                    self._lease_lost_message(run_id, task_id, owner, generation)
                )
            self._commit(connection)
            return available_at
        except Exception:
            self._rollback(connection)
            raise
        finally:
            connection.close()

    def quarantine(
        self,
        run_id: str,
        task_id: str,
        *,
        owner: str,
        generation: int,
        error_type: str,
        result: Any = None,
        now: float | datetime | None = None,
    ) -> None:
        """Permanently quarantine a claimed task under its fencing token."""

        run_id = _require_identifier(run_id, label="run_id")
        task_id = _require_identifier(task_id, label="task_id")
        owner = _require_identifier(owner, label="owner")
        error_type = _require_identifier(error_type, label="error_type")
        generation = self._validate_generation(generation)
        timestamp = _epoch(now)
        result_json = _json_text(result, label="quarantine result")
        self._fenced_update(
            run_id,
            task_id,
            owner=owner,
            generation=generation,
            sql="""
                UPDATE model_run_tasks
                SET status = 'quarantined', result_json = ?, error_type = ?,
                    lease_owner = NULL, lease_expires_at = NULL, updated_at = ?
                WHERE run_id = ? AND task_id = ? AND status = 'running'
                  AND lease_owner = ? AND claim_generation = ?
            """,
            parameters=(
                result_json,
                error_type,
                timestamp,
                run_id,
                task_id,
                owner,
                generation,
            ),
        )

    def counts(self, run_id: str) -> dict[str, int]:
        """Return deterministic status counts plus ``total`` for a run."""

        run_id = _require_identifier(run_id, label="run_id")
        connection = self._connect()
        try:
            exists = connection.execute(
                "SELECT 1 FROM model_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if exists is None:
                raise RunNotFoundError(f"Unknown run_id {run_id!r}.")
            rows = connection.execute(
                """
                SELECT status, COUNT(*) AS task_count
                FROM model_run_tasks
                WHERE run_id = ?
                GROUP BY status
                """,
                (run_id,),
            ).fetchall()
        finally:
            connection.close()
        result = {status: 0 for status in TASK_STATUSES}
        for row in rows:
            result[str(row["status"])] = int(row["task_count"])
        result["total"] = sum(result.values())
        return result

    def counts_by_kind(self, run_id: str) -> dict[str, dict[str, int]]:
        """Return status counts grouped by immutable task kind."""

        run_id = _require_identifier(run_id, label="run_id")
        connection = self._connect()
        try:
            exists = connection.execute(
                "SELECT 1 FROM model_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if exists is None:
                raise RunNotFoundError(f"Unknown run_id {run_id!r}.")
            rows = connection.execute(
                """
                SELECT kind, status, COUNT(*) AS task_count
                FROM model_run_tasks
                WHERE run_id = ?
                GROUP BY kind, status
                ORDER BY kind, status
                """,
                (run_id,),
            ).fetchall()
        finally:
            connection.close()
        result: dict[str, dict[str, int]] = {}
        for row in rows:
            kind = str(row["kind"])
            if kind not in result:
                result[kind] = {status: 0 for status in TASK_STATUSES}
            result[kind][str(row["status"])] = int(row["task_count"])
        for counts in result.values():
            counts["total"] = sum(counts[status] for status in TASK_STATUSES)
        return result

    def list_tasks(
        self,
        run_id: str,
        *,
        statuses: Iterable[str] | None = None,
    ) -> list[TaskRecord]:
        """List tasks in frozen plan order, optionally filtered by status."""

        run_id = _require_identifier(run_id, label="run_id")
        selected: tuple[str, ...] | None = None
        if statuses is not None:
            selected = tuple(dict.fromkeys(statuses))
            if not selected:
                return []
            invalid = set(selected) - set(TASK_STATUSES)
            if invalid:
                raise ValueError(f"Unknown task statuses: {sorted(invalid)!r}.")
        connection = self._connect()
        try:
            exists = connection.execute(
                "SELECT 1 FROM model_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if exists is None:
                raise RunNotFoundError(f"Unknown run_id {run_id!r}.")
            parameters: list[Any] = [run_id]
            predicate = ""
            if selected is not None:
                placeholders = ", ".join("?" for _ in selected)
                predicate = f" AND status IN ({placeholders})"
                parameters.extend(selected)
            rows = connection.execute(
                f"""
                SELECT run_id, task_id, kind, payload_json, status, attempt,
                       available_at, lease_owner, lease_expires_at,
                       claim_generation, result_json, error_type
                FROM model_run_tasks
                WHERE run_id = ?{predicate}
                ORDER BY plan_index
                """,  # noqa: S608 - placeholders are generated, not user-controlled.
                parameters,
            ).fetchall()
        finally:
            connection.close()
        return [_row_to_record(row) for row in rows]

    @staticmethod
    def _validate_generation(generation: int) -> int:
        if isinstance(generation, bool) or not isinstance(generation, int) or generation <= 0:
            raise ValueError("generation must be a positive integer.")
        return generation

    @staticmethod
    def _lease_lost_message(
        run_id: str,
        task_id: str,
        owner: str,
        generation: int,
    ) -> str:
        return (
            "Lease is no longer current for "
            f"run_id={run_id!r}, task_id={task_id!r}, "
            f"owner={owner!r}, generation={generation}."
        )

    def _fenced_update(
        self,
        run_id: str,
        task_id: str,
        *,
        owner: str,
        generation: int,
        sql: str,
        parameters: Sequence[Any],
    ) -> None:
        connection = self._connect()
        try:
            self._begin_immediate(connection)
            cursor = connection.execute(sql, parameters)
            if cursor.rowcount != 1:
                raise LeaseLostError(
                    self._lease_lost_message(run_id, task_id, owner, generation)
                )
            self._commit(connection)
        except Exception:
            self._rollback(connection)
            raise
        finally:
            connection.close()
