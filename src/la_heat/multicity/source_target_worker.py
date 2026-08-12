"""Resumable worker for the authorized Los Angeles source-target lane.

The worker shares the frozen 159-unit target queue, but it can claim only the
90 Los Angeles overpasses and their single city compile.  The external cohort
and final merge therefore remain untouched until a later authorization.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Protocol

from la_heat.model_run_queue import LeaseLostError, ModelRunQueue, TaskRecord
from la_heat.multicity.target_engine import CITY_COMMIT, MulticityTargetEngine
from la_heat.multicity.target_runtime import (
    DEFAULT_DATABASE,
    EXTERNAL_KINDS,
    FINAL_KINDS,
    SOURCE_KINDS,
    target_run_id,
    task_specs_from_target_plan,
)
from la_heat.multicity.target_transaction import (
    SOURCE_CITY_ID,
    SOURCE_LANE,
    stage_multicity_target_build_plan,
)
from la_heat.provenance import canonical_sha256, sha256_file

DEFAULT_AUTHORIZATION: Final = Path("manifests/multicity/targets/SOURCE_TARGET_AUTHORIZATION.json")
DEFAULT_STATUS: Final = Path("data/interim/multicity/targets/runtime/source_worker_status.json")
SOURCE_COMPLETION: Final = Path("manifests/multicity/targets/LA_SOURCE_TARGETS_COMPLETE.json")
EXPECTED_SOURCE_OVERPASSES: Final = 90
EXPECTED_SOURCE_COMPILES: Final = 1
EXPECTED_EXTERNAL_TASKS: Final = 68


class SourceTargetWorkerError(RuntimeError):
    """Raised when the source worker cannot preserve the frozen task boundary."""


class TargetExecutor(Protocol):
    """Small interface implemented by :class:`MulticityTargetEngine`."""

    def execute(self, payload: dict[str, Any]) -> dict[str, Any]: ...


EngineFactory = Callable[[], TargetExecutor]
CompletionPublisher = Callable[[ModelRunQueue, str], Mapping[str, Any]]


@dataclass(frozen=True, slots=True)
class SourceWorkerSettings:
    """Runtime settings for one resumable source-lane session."""

    workers: int = 1
    lease_seconds: float = 600.0
    heartbeat_interval_seconds: float = 60.0
    retry_base_seconds: float = 5.0
    retry_max_seconds: float = 300.0
    poll_seconds: float = 0.5

    def validate(self) -> None:
        if isinstance(self.workers, bool) or not 1 <= self.workers <= 16:
            raise ValueError("workers must be an integer from 1 through 16")
        values = {
            "lease_seconds": self.lease_seconds,
            "heartbeat_interval_seconds": self.heartbeat_interval_seconds,
            "retry_base_seconds": self.retry_base_seconds,
            "retry_max_seconds": self.retry_max_seconds,
            "poll_seconds": self.poll_seconds,
        }
        if any(float(value) <= 0 for value in values.values()):
            raise ValueError("worker timing values must be greater than zero")
        if self.heartbeat_interval_seconds >= self.lease_seconds:
            raise ValueError("heartbeat interval must be shorter than the lease")
        if self.retry_max_seconds < self.retry_base_seconds:
            raise ValueError("maximum retry delay cannot be shorter than the base delay")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _resolved_inside(root: Path, value: str | Path, *, label: str) -> Path:
    raw = Path(value)
    path = raw.resolve() if raw.is_absolute() else (root / raw).resolve()
    if not path.is_relative_to(root):
        raise SourceTargetWorkerError(f"{label} must stay inside the project")
    return path


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(dict(payload), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _kind_counts(queue: ModelRunQueue, run_id: str, kind: str) -> dict[str, int]:
    observed = queue.counts_by_kind(run_id).get(kind)
    if observed is None:
        return {
            "pending": 0,
            "running": 0,
            "complete": 0,
            "quarantined": 0,
            "total": 0,
        }
    return dict(observed)


def _combined_counts(*groups: Mapping[str, int]) -> dict[str, int]:
    keys = ("pending", "running", "complete", "quarantined", "total")
    return {key: sum(int(group.get(key, 0)) for group in groups) for key in keys}


def _validate_frozen_queue(queue: ModelRunQueue, run_id: str) -> None:
    by_kind = queue.counts_by_kind(run_id)
    expected = {
        "source_overpass": EXPECTED_SOURCE_OVERPASSES,
        "source_compile": EXPECTED_SOURCE_COMPILES,
        "external_overpass": 64,
        "external_compile": 3,
        "final_merge": 1,
    }
    observed = {kind: int(by_kind.get(kind, {}).get("total", 0)) for kind in expected}
    if observed != expected or set(by_kind) != set(expected):
        raise SourceTargetWorkerError("The frozen 159-unit target queue changed")


def _source_phase(queue: ModelRunQueue, run_id: str) -> str:
    overpasses = _kind_counts(queue, run_id, "source_overpass")
    compile_count = _kind_counts(queue, run_id, "source_compile")
    if overpasses["quarantined"] or compile_count["quarantined"]:
        raise SourceTargetWorkerError("Source tasks may not be quarantined")
    if overpasses["complete"] < overpasses["total"]:
        return "source_overpass"
    if overpasses["running"] or overpasses["pending"]:
        return "source_overpass"
    if compile_count["complete"] < compile_count["total"]:
        return "source_compile"
    return "complete"


def safe_source_status(
    queue: ModelRunQueue,
    run_id: str,
    *,
    workers: int,
    active_task_ids: tuple[str, ...] = (),
    last_error_type: str | None = None,
    retry_count: int = 0,
    completion_manifest: str | None = None,
) -> dict[str, Any]:
    """Return status containing no payloads, URLs, tokens, or exception text."""

    overpasses = _kind_counts(queue, run_id, "source_overpass")
    compile_count = _kind_counts(queue, run_id, "source_compile")
    external = _combined_counts(
        *(_kind_counts(queue, run_id, kind) for kind in sorted(EXTERNAL_KINDS | FINAL_KINDS))
    )
    source = _combined_counts(overpasses, compile_count)
    desired = queue.get_desired_state(run_id)
    phase = _source_phase(queue, run_id)
    if phase == "complete":
        state = "complete"
    elif desired == "paused" and active_task_ids:
        state = "pausing"
    elif desired == "paused":
        state = "paused"
    elif last_error_type and not active_task_ids:
        state = "retry_wait"
    else:
        state = "running"
    return {
        "schema_version": 1,
        "state": state,
        "run_id": run_id,
        "desired_state": desired,
        "workers": workers,
        "phase": phase,
        "source_counts": {
            "overpass": overpasses,
            "compile": compile_count,
            "total": source,
        },
        "external_counts": external,
        "active_task_ids": list(active_task_ids),
        "last_error_type": last_error_type,
        "retry_count": retry_count,
        "completion_manifest": completion_manifest,
        "updated_at_utc": _utc_now(),
    }


class _Heartbeat:
    def __init__(
        self,
        queue: ModelRunQueue,
        task: TaskRecord,
        *,
        interval_seconds: float,
        lease_seconds: float,
    ) -> None:
        self.queue = queue
        self.task = task
        self.interval_seconds = interval_seconds
        self.lease_seconds = lease_seconds
        self.stop_event = threading.Event()
        self.lease_lost = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self.stop_event.wait(self.interval_seconds):
            try:
                self.queue.heartbeat(
                    self.task.run_id,
                    self.task.task_id,
                    owner=str(self.task.lease_owner),
                    generation=self.task.claim_generation,
                    lease_seconds=self.lease_seconds,
                )
            except Exception:
                self.lease_lost.set()
                return

    def __enter__(self) -> _Heartbeat:
        self.thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop_event.set()
        self.thread.join()


def execute_source_queue(
    *,
    database_path: str | Path,
    run_id: str,
    status_path: str | Path,
    engine_factory: EngineFactory,
    settings: SourceWorkerSettings | None = None,
    completion_publisher: CompletionPublisher | None = None,
) -> dict[str, Any]:
    """Run source tasks until complete or cooperatively paused."""

    settings = SourceWorkerSettings() if settings is None else settings
    settings.validate()
    database = Path(database_path).resolve()
    status = Path(status_path).resolve()
    coordinator_queue = ModelRunQueue(database)
    _validate_frozen_queue(coordinator_queue, run_id)
    state_lock = threading.Lock()
    active: set[str] = set()
    last_error_type: str | None = None
    retry_count = 0
    session_started = time.monotonic()
    initial_completed = _combined_counts(
        _kind_counts(coordinator_queue, run_id, "source_overpass"),
        _kind_counts(coordinator_queue, run_id, "source_compile"),
    )["complete"]

    def publish_status(completion: str | None = None) -> dict[str, Any]:
        nonlocal last_error_type, retry_count
        with state_lock:
            payload = safe_source_status(
                coordinator_queue,
                run_id,
                workers=settings.workers,
                active_task_ids=tuple(sorted(active)),
                last_error_type=last_error_type,
                retry_count=retry_count,
                completion_manifest=completion,
            )
            completed = int(payload["source_counts"]["total"]["complete"])
            remaining = (
                int(payload["source_counts"]["total"]["total"])
                - completed
            )
            session_completed = completed - initial_completed
            elapsed = max(0.0, time.monotonic() - session_started)
            payload["eta_seconds"] = (
                0.0
                if remaining == 0
                else (
                    elapsed * remaining / session_completed
                    if session_completed > 0
                    else None
                )
            )
            _write_json(status, payload)
            return payload

    publish_status()

    def worker(index: int) -> None:
        nonlocal last_error_type, retry_count
        queue = ModelRunQueue(database)
        engine: TargetExecutor | None = None
        owner = f"source-{os.getpid()}-{index}-{uuid.uuid4().hex[:12]}"
        while True:
            if queue.get_desired_state(run_id) == "paused":
                return
            phase = _source_phase(queue, run_id)
            if phase == "complete":
                return
            claim = queue.claim_next(
                run_id,
                owner=owner,
                lease_seconds=settings.lease_seconds,
                kinds={phase},
            )
            if claim is None:
                time.sleep(settings.poll_seconds)
                continue
            with state_lock:
                active.add(claim.task_id)
            publish_status()
            heartbeat = _Heartbeat(
                queue,
                claim,
                interval_seconds=settings.heartbeat_interval_seconds,
                lease_seconds=settings.lease_seconds,
            )
            try:
                if engine is None:
                    engine = engine_factory()
                with heartbeat:
                    result = engine.execute(dict(claim.payload))
                if heartbeat.lease_lost.is_set():
                    raise LeaseLostError("lease heartbeat was lost")
                queue.complete(
                    run_id,
                    claim.task_id,
                    owner=owner,
                    generation=claim.claim_generation,
                    result=result,
                )
                with state_lock:
                    last_error_type = None
            except LeaseLostError:
                with state_lock:
                    last_error_type = "LeaseLostError"
            except Exception as error:
                error_type = type(error).__name__
                try:
                    queue.retry(
                        run_id,
                        claim.task_id,
                        owner=owner,
                        generation=claim.claim_generation,
                        error_type=error_type,
                        base_delay_seconds=settings.retry_base_seconds,
                        max_delay_seconds=settings.retry_max_seconds,
                    )
                    with state_lock:
                        retry_count += 1
                        last_error_type = error_type
                except LeaseLostError:
                    with state_lock:
                        last_error_type = "LeaseLostError"
            finally:
                with state_lock:
                    active.discard(claim.task_id)
                publish_status()

    with ThreadPoolExecutor(
        max_workers=settings.workers,
        thread_name_prefix="source-target",
    ) as pool:
        futures = [pool.submit(worker, index) for index in range(settings.workers)]
        for future in futures:
            future.result()

    completion_path: str | None = None
    if _source_phase(coordinator_queue, run_id) == "complete":
        if completion_publisher is not None:
            completion = completion_publisher(coordinator_queue, run_id)
            path_value = completion.get("path")
            completion_path = str(path_value) if path_value is not None else None
        coordinator_queue.set_desired_state(run_id, "paused")
    return publish_status(completion_path)


def _file_record(root: Path, path: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _read_committed(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SourceTargetWorkerError(f"{label} is unavailable") from error
    if not isinstance(payload, dict):
        raise SourceTargetWorkerError(f"{label} is not a JSON object")
    recorded = payload.get("commit_sha256")
    unsigned = dict(payload)
    unsigned.pop("commit_sha256", None)
    if not isinstance(recorded, str) or recorded != canonical_sha256(unsigned):
        raise SourceTargetWorkerError(f"{label} failed commit authentication")
    return payload


def publish_source_completion(
    project_root: str | Path,
    queue: ModelRunQueue,
    run_id: str,
    engine: MulticityTargetEngine,
    *,
    output_path: str | Path = SOURCE_COMPLETION,
) -> dict[str, Any]:
    """Publish or authenticate the append-only LA source completion record."""

    root = Path(project_root).resolve()
    output = _resolved_inside(root, output_path, label="Completion manifest")
    source_tasks = [task for task in queue.list_tasks(run_id) if task.kind in SOURCE_KINDS]
    if len(source_tasks) != EXPECTED_SOURCE_OVERPASSES + EXPECTED_SOURCE_COMPILES or any(
        task.status != "complete" for task in source_tasks
    ):
        raise SourceTargetWorkerError("All 91 source tasks must complete before publication")
    external_tasks = [
        task for task in queue.list_tasks(run_id) if task.kind in EXTERNAL_KINDS | FINAL_KINDS
    ]
    if len(external_tasks) != EXPECTED_EXTERNAL_TASKS or any(
        task.status != "pending"
        or task.attempt != 0
        or task.result is not None
        or task.error_type is not None
        for task in external_tasks
    ):
        raise SourceTargetWorkerError("External target tasks were not left untouched")
    result_commits: list[dict[str, str]] = []
    for task in source_tasks:
        result = task.result
        commit = result.get("commit_sha256") if isinstance(result, dict) else None
        if not isinstance(commit, str) or len(commit) != 64:
            raise SourceTargetWorkerError("A source task lacks its output commit")
        result_commits.append({"task_id": task.task_id, "commit_sha256": commit})

    city_commit_path = engine.cache_root / "cities" / SOURCE_CITY_ID / CITY_COMMIT
    city_commit = _read_committed(city_commit_path, label="LA city target commit")
    output_files = city_commit.get("output_files")
    if not isinstance(output_files, dict):
        raise SourceTargetWorkerError("LA city target commit lacks output files")
    for name, record in output_files.items():
        target = city_commit_path.parent / str(name)
        if (
            not isinstance(record, dict)
            or not target.is_file()
            or target.stat().st_size != record.get("bytes")
            or sha256_file(target) != record.get("sha256")
        ):
            raise SourceTargetWorkerError("An LA target output failed authentication")
    marker = engine.authorization.values_opened_marker
    marker_payload = _read_committed(marker, label="Source VALUES_OPENED marker")
    if marker_payload.get("authorization_commit_sha256") != engine.authorization.commit_sha256:
        raise SourceTargetWorkerError("Source VALUES_OPENED marker belongs to another claim")

    payload: dict[str, Any] = {
        "schema_version": 1,
        "state": "la_source_targets_complete",
        "lane": SOURCE_LANE,
        "city_ids": [SOURCE_CITY_ID],
        "run_id": run_id,
        "claim_id_sha256": hashlib.sha256(
            engine.authorization.claim_id.encode("utf-8")
        ).hexdigest(),
        "plan_commit_sha256": engine.authorization.plan_commit_sha256,
        "authorization": {
            **_file_record(root, engine.authorization.path),
            "commit_sha256": engine.authorization.commit_sha256,
        },
        "values_opened_marker": {
            **_file_record(root, marker),
            "commit_sha256": marker_payload["commit_sha256"],
        },
        "source_work_units": {
            "overpass": EXPECTED_SOURCE_OVERPASSES,
            "compile": EXPECTED_SOURCE_COMPILES,
            "total": len(source_tasks),
            "result_commits_sha256": canonical_sha256(result_commits),
        },
        "city_target_commit": {
            **_file_record(root, city_commit_path),
            "commit_sha256": city_commit["commit_sha256"],
            "output_files": output_files,
        },
        "external_cohort": {
            "task_count": len(external_tasks),
            "tasks_claimed": False,
            "target_values_read": False,
        },
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    encoded = (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError:
        observed = _read_committed(output, label="LA source completion manifest")
        if observed != payload:
            raise SourceTargetWorkerError("LA source completion manifest is append-only") from None
    else:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    return {"path": output.relative_to(root).as_posix(), **payload}


def run_authorized_source_worker(
    project_root: str | Path,
    *,
    authorization_path: str | Path = DEFAULT_AUTHORIZATION,
    database_path: str | Path = DEFAULT_DATABASE,
    status_path: str | Path = DEFAULT_STATUS,
    settings: SourceWorkerSettings | None = None,
    start: bool = False,
) -> dict[str, Any]:
    """Authenticate the frozen plan, then run only its authorized source lane."""

    root = Path(project_root).resolve()
    settings = SourceWorkerSettings() if settings is None else settings
    database = _resolved_inside(root, database_path, label="Target queue")
    status = _resolved_inside(root, status_path, label="Worker status")
    authorization = _resolved_inside(root, authorization_path, label="Authorization")
    plan = stage_multicity_target_build_plan(root, check_only=True)
    run_id = target_run_id(plan)
    queue = ModelRunQueue(database)
    queue.initialize_run(
        run_id,
        task_specs_from_target_plan(plan),
        desired_state="paused",
    )
    _validate_frozen_queue(queue, run_id)
    if start:
        queue.set_desired_state(run_id, "running")

    def engine_factory() -> MulticityTargetEngine:
        return MulticityTargetEngine.create(
            root,
            lane=SOURCE_LANE,
            authorization_path=authorization,
        )

    def completion_publisher(
        completion_queue: ModelRunQueue,
        completion_run_id: str,
    ) -> Mapping[str, Any]:
        engine = engine_factory()
        return publish_source_completion(
            root,
            completion_queue,
            completion_run_id,
            engine,
        )

    return execute_source_queue(
        database_path=database,
        run_id=run_id,
        status_path=status,
        engine_factory=engine_factory,
        settings=settings,
        completion_publisher=completion_publisher,
    )
