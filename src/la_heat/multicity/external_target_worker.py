"""Resumable worker restricted to the indivisible three-city external lane."""

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
from la_heat.multicity.external_target_authorization import (
    AUTHORIZATION_PATH,
    authenticate_external_target_authorization,
)
from la_heat.multicity.portable_predictor_inventory import EXTERNAL_CITY_IDS
from la_heat.multicity.target_engine import CITY_COMMIT, MulticityTargetEngine
from la_heat.multicity.target_runtime import (
    DEFAULT_DATABASE,
    target_run_id,
    task_specs_from_target_plan,
)
from la_heat.multicity.target_transaction import EXTERNAL_LANE, stage_multicity_target_build_plan
from la_heat.provenance import canonical_sha256, sha256_file

DEFAULT_STATUS: Final = Path(
    "data/interim/multicity/targets/runtime/external_worker_status.json"
)
EXTERNAL_COMPLETION: Final = Path(
    "manifests/multicity/targets/THREE_CITY_EXTERNAL_TARGETS_COMPLETE.json"
)
EXPECTED_COUNTS: Final = {
    "source_overpass": 90,
    "source_compile": 1,
    "external_overpass": 64,
    "external_compile": 3,
    "final_merge": 1,
}
EXTERNAL_KINDS: Final = frozenset({"external_overpass", "external_compile"})


class ExternalTargetWorkerError(RuntimeError):
    """Raised when a worker would violate the frozen external-lane boundary."""


class TargetExecutor(Protocol):
    def execute(self, payload: dict[str, Any]) -> dict[str, Any]: ...


EngineFactory = Callable[[], TargetExecutor]
CompletionPublisher = Callable[[ModelRunQueue, str], Mapping[str, Any]]


@dataclass(frozen=True, slots=True)
class ExternalWorkerSettings:
    workers: int = 1
    lease_seconds: float = 600.0
    heartbeat_interval_seconds: float = 60.0
    retry_base_seconds: float = 5.0
    retry_max_seconds: float = 300.0
    poll_seconds: float = 0.5

    def validate(self) -> None:
        if isinstance(self.workers, bool) or not 1 <= self.workers <= 16:
            raise ValueError("workers must be an integer from 1 through 16")
        if any(
            float(value) <= 0
            for value in (
                self.lease_seconds,
                self.heartbeat_interval_seconds,
                self.retry_base_seconds,
                self.retry_max_seconds,
                self.poll_seconds,
            )
        ):
            raise ValueError("worker timing values must be positive")
        if self.heartbeat_interval_seconds >= self.lease_seconds:
            raise ValueError("heartbeat interval must be shorter than the lease")
        if self.retry_max_seconds < self.retry_base_seconds:
            raise ValueError("retry maximum cannot be below retry base")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _inside(root: Path, value: str | Path, *, label: str) -> Path:
    raw = Path(value)
    path = raw.resolve() if raw.is_absolute() else (root / raw).resolve()
    if not path.is_relative_to(root):
        raise ExternalTargetWorkerError(f"{label} must stay inside the project")
    return path


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(dict(payload), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _counts(queue: ModelRunQueue, run_id: str, kind: str) -> dict[str, int]:
    observed = queue.counts_by_kind(run_id).get(kind, {})
    return {
        name: int(observed.get(name, 0))
        for name in ("pending", "running", "complete", "quarantined", "total")
    }


def _combined(*groups: Mapping[str, int]) -> dict[str, int]:
    return {
        name: sum(int(group.get(name, 0)) for group in groups)
        for name in ("pending", "running", "complete", "quarantined", "total")
    }


def validate_external_queue(queue: ModelRunQueue, run_id: str) -> None:
    by_kind = queue.counts_by_kind(run_id)
    observed = {
        kind: int(by_kind.get(kind, {}).get("total", 0)) for kind in EXPECTED_COUNTS
    }
    if observed != EXPECTED_COUNTS or set(by_kind) != set(EXPECTED_COUNTS):
        raise ExternalTargetWorkerError("The frozen 159-unit target queue changed")
    source = _combined(
        _counts(queue, run_id, "source_overpass"),
        _counts(queue, run_id, "source_compile"),
    )
    if source != {
        "pending": 0,
        "running": 0,
        "complete": 91,
        "quarantined": 0,
        "total": 91,
    }:
        raise ExternalTargetWorkerError("All 91 LA source tasks must complete first")
    final = queue.list_tasks(run_id, statuses=("pending",))
    final = [task for task in final if task.kind == "final_merge"]
    if len(final) != 1 or final[0].attempt != 0 or final[0].result is not None:
        raise ExternalTargetWorkerError("Final merge must remain untouched")


def _phase(queue: ModelRunQueue, run_id: str) -> str:
    overpass = _counts(queue, run_id, "external_overpass")
    compile_count = _counts(queue, run_id, "external_compile")
    if overpass["quarantined"] or compile_count["quarantined"]:
        raise ExternalTargetWorkerError("External tasks may not be quarantined")
    if overpass["complete"] < 64 or overpass["pending"] or overpass["running"]:
        return "external_overpass"
    if compile_count["complete"] < 3:
        return "external_compile"
    return "complete"


def safe_external_status(
    queue: ModelRunQueue,
    run_id: str,
    *,
    workers: int,
    active_task_ids: tuple[str, ...] = (),
    last_error_type: str | None = None,
    retry_count: int = 0,
    completion_manifest: str | None = None,
) -> dict[str, Any]:
    """Return progress without payloads, URLs, target values, or error text."""

    overpass = _counts(queue, run_id, "external_overpass")
    compile_count = _counts(queue, run_id, "external_compile")
    external = _combined(overpass, compile_count)
    phase = _phase(queue, run_id)
    desired = queue.get_desired_state(run_id)
    state = (
        "complete"
        if phase == "complete"
        else "pausing"
        if desired == "paused" and active_task_ids
        else "paused"
        if desired == "paused"
        else "retry_wait"
        if last_error_type and not active_task_ids
        else "running"
    )
    return {
        "schema_version": 1,
        "state": state,
        "run_id": run_id,
        "desired_state": desired,
        "workers": workers,
        "phase": phase,
        "external_counts": {
            "overpass": overpass,
            "compile": compile_count,
            "total": external,
        },
        "source_complete": True,
        "final_merge_claimed": False,
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
        self.interval = interval_seconds
        self.lease = lease_seconds
        self.stop = threading.Event()
        self.lost = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self.stop.wait(self.interval):
            try:
                self.queue.heartbeat(
                    self.task.run_id,
                    self.task.task_id,
                    owner=str(self.task.lease_owner),
                    generation=self.task.claim_generation,
                    lease_seconds=self.lease,
                )
            except Exception:
                self.lost.set()
                return

    def __enter__(self) -> _Heartbeat:
        self.thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.stop.set()
        self.thread.join()


def execute_external_queue(
    *,
    database_path: str | Path,
    run_id: str,
    status_path: str | Path,
    engine_factory: EngineFactory,
    settings: ExternalWorkerSettings | None = None,
    completion_publisher: CompletionPublisher | None = None,
) -> dict[str, Any]:
    """Claim only 64 external overpasses and three city compiles."""

    settings = ExternalWorkerSettings() if settings is None else settings
    settings.validate()
    database = Path(database_path).resolve()
    status_path = Path(status_path).resolve()
    coordinator = ModelRunQueue(database)
    validate_external_queue(coordinator, run_id)
    lock = threading.Lock()
    active: set[str] = set()
    retry_count = 0
    last_error: str | None = None
    started = time.monotonic()
    initial_complete = _combined(
        _counts(coordinator, run_id, "external_overpass"),
        _counts(coordinator, run_id, "external_compile"),
    )["complete"]

    def publish(completion: str | None = None) -> dict[str, Any]:
        with lock:
            payload = safe_external_status(
                coordinator,
                run_id,
                workers=settings.workers,
                active_task_ids=tuple(sorted(active)),
                last_error_type=last_error,
                retry_count=retry_count,
                completion_manifest=completion,
            )
            counts = payload["external_counts"]["total"]
            session_complete = int(counts["complete"]) - initial_complete
            remaining = int(counts["total"]) - int(counts["complete"])
            elapsed = max(0.0, time.monotonic() - started)
            payload["eta_seconds"] = (
                0.0
                if remaining == 0
                else elapsed * remaining / session_complete
                if session_complete > 0
                else None
            )
            _write_json(status_path, payload)
            return payload

    publish()

    def worker(index: int) -> None:
        nonlocal retry_count, last_error
        queue = ModelRunQueue(database)
        engine: TargetExecutor | None = None
        owner = f"external-{os.getpid()}-{index}-{uuid.uuid4().hex[:12]}"
        while queue.get_desired_state(run_id) == "running":
            phase = _phase(queue, run_id)
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
            with lock:
                active.add(claim.task_id)
            publish()
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
                if heartbeat.lost.is_set():
                    raise LeaseLostError("lease heartbeat was lost")
                queue.complete(
                    run_id,
                    claim.task_id,
                    owner=owner,
                    generation=claim.claim_generation,
                    result=result,
                )
                with lock:
                    last_error = None
            except LeaseLostError:
                with lock:
                    last_error = "LeaseLostError"
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
                    with lock:
                        retry_count += 1
                        last_error = error_type
                except LeaseLostError:
                    with lock:
                        last_error = "LeaseLostError"
            finally:
                with lock:
                    active.discard(claim.task_id)
                publish()

    with ThreadPoolExecutor(
        max_workers=settings.workers, thread_name_prefix="external-target"
    ) as pool:
        futures = [pool.submit(worker, index) for index in range(settings.workers)]
        for future in futures:
            future.result()

    completion_path: str | None = None
    if _phase(coordinator, run_id) == "complete":
        if completion_publisher is not None:
            completion = completion_publisher(coordinator, run_id)
            completion_path = str(completion.get("path"))
        coordinator.set_desired_state(run_id, "paused")
    return publish(completion_path)


def _read_committed(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ExternalTargetWorkerError(f"{label} is unavailable") from error
    unsigned = dict(payload)
    recorded = unsigned.pop("commit_sha256", None)
    if not isinstance(recorded, str) or recorded != canonical_sha256(unsigned):
        raise ExternalTargetWorkerError(f"{label} commit is invalid")
    return payload


def _file_record(root: Path, path: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def publish_external_completion(
    project_root: str | Path,
    queue: ModelRunQueue,
    run_id: str,
    engine: MulticityTargetEngine,
    *,
    output_path: str | Path = EXTERNAL_COMPLETION,
) -> dict[str, Any]:
    """Publish three city compiles while proving final merge stayed pending."""

    root = Path(project_root).resolve()
    output = _inside(root, output_path, label="External completion")
    validate_external_queue(queue, run_id)
    external = [task for task in queue.list_tasks(run_id) if task.kind in EXTERNAL_KINDS]
    if len(external) != 67 or any(task.status != "complete" for task in external):
        raise ExternalTargetWorkerError("All 67 external tasks must complete")
    final = [task for task in queue.list_tasks(run_id) if task.kind == "final_merge"]
    if (
        len(final) != 1
        or final[0].status != "pending"
        or final[0].attempt != 0
        or final[0].result is not None
    ):
        raise ExternalTargetWorkerError("External worker touched final merge")
    authorization = authenticate_external_target_authorization(
        root, engine.authorization.path
    )
    marker = engine.authorization.values_opened_marker
    marker_payload = _read_committed(marker, label="External VALUES_OPENED")
    if (
        marker_payload.get("authorization_commit_sha256")
        != engine.authorization.commit_sha256
        or marker_payload.get("external_prediction_commit_sha256")
        != authorization["external_prediction_commit_sha256"]
    ):
        raise ExternalTargetWorkerError("External marker belongs to another claim")
    city_targets: dict[str, Any] = {}
    for city_id in EXTERNAL_CITY_IDS:
        commit_path = engine.cache_root / "cities" / city_id / CITY_COMMIT
        commit = _read_committed(commit_path, label=f"{city_id} target compile")
        files = commit.get("output_files")
        if not isinstance(files, dict) or "targets.parquet" not in files:
            raise ExternalTargetWorkerError("City compile lacks target output")
        for name, record in files.items():
            path = commit_path.parent / name
            if (
                not isinstance(record, dict)
                or not path.is_file()
                or record.get("bytes") != path.stat().st_size
                or record.get("sha256") != sha256_file(path)
            ):
                raise ExternalTargetWorkerError("City target output failed authentication")
        city_targets[city_id] = {
            **_file_record(root, commit_path),
            "commit_sha256": commit["commit_sha256"],
            "directory": commit_path.parent.relative_to(root).as_posix(),
            "output_files": files,
        }
    result_commits = [
        {"task_id": task.task_id, "commit_sha256": task.result["commit_sha256"]}
        for task in external
        if isinstance(task.result, dict)
    ]
    if len(result_commits) != 67:
        raise ExternalTargetWorkerError("External tasks lack output commits")
    payload: dict[str, Any] = {
        "schema_version": 1,
        "state": "three_city_external_targets_complete",
        "lane": EXTERNAL_LANE,
        "city_ids": list(EXTERNAL_CITY_IDS),
        "run_id": run_id,
        "claim_id_sha256": hashlib.sha256(
            engine.authorization.claim_id.encode("utf-8")
        ).hexdigest(),
        "authorization": {
            **_file_record(root, engine.authorization.path),
            "commit_sha256": engine.authorization.commit_sha256,
        },
        "external_prediction_commit_sha256": authorization[
            "external_prediction_commit_sha256"
        ],
        "values_opened_marker": {
            **_file_record(root, marker),
            "commit_sha256": marker_payload["commit_sha256"],
        },
        "external_work_units": {
            "overpass": 64,
            "city_compile": 3,
            "total": 67,
            "result_commits_sha256": canonical_sha256(result_commits),
        },
        "city_targets": city_targets,
        "source_tasks_complete": True,
        "final_merge": {"claimed": False, "attempt": 0, "status": "pending"},
        "next_safe_stage": "join_committed_predictions_and_run_frozen_external_evaluator",
    }
    payload["commit_sha256"] = canonical_sha256(payload)
    output.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode()
    try:
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError:
        if _read_committed(output, label="External completion") != payload:
            raise ExternalTargetWorkerError("External completion is append-only") from None
    else:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
    return {"path": output.relative_to(root).as_posix(), **payload}


def run_authorized_external_worker(
    project_root: str | Path,
    *,
    authorization_path: str | Path = AUTHORIZATION_PATH,
    database_path: str | Path = DEFAULT_DATABASE,
    status_path: str | Path = DEFAULT_STATUS,
    settings: ExternalWorkerSettings | None = None,
    start: bool = False,
) -> dict[str, Any]:
    """Authenticate the sole claim, then execute only its external lane."""

    root = Path(project_root).resolve()
    authorization_path = _inside(root, authorization_path, label="Authorization")
    authenticate_external_target_authorization(root, authorization_path)
    database = _inside(root, database_path, label="Queue")
    status = _inside(root, status_path, label="Status")
    plan = stage_multicity_target_build_plan(root, check_only=True)
    run_id = target_run_id(plan)
    queue = ModelRunQueue(database)
    queue.initialize_run(run_id, task_specs_from_target_plan(plan), desired_state="paused")
    validate_external_queue(queue, run_id)
    if start:
        queue.set_desired_state(run_id, "running")

    def engine_factory() -> MulticityTargetEngine:
        return MulticityTargetEngine.create(
            root,
            lane=EXTERNAL_LANE,
            authorization_path=authorization_path,
        )

    return execute_external_queue(
        database_path=database,
        run_id=run_id,
        status_path=status,
        engine_factory=engine_factory,
        settings=settings,
        completion_publisher=lambda completion_queue, completion_run_id: (
            publish_external_completion(
                root,
                completion_queue,
                completion_run_id,
                engine_factory(),
            )
        ),
    )
