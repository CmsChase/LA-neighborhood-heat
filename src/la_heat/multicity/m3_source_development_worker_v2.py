"""Single-compute-worker runner for the local M3 integrity overlay."""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Protocol

from la_heat.model_run_queue import LeaseLostError, ModelRunQueue
from la_heat.multicity.m3_source_development_engine_v2 import (
    LOGICAL_PHASE,
    PHASES,
    QA_PHASE,
)
from la_heat.multicity.m3_source_development_runtime_v2 import (
    RunnerSettingsV2,
    initialize_source_runtime_v2,
    load_runner_settings_v2,
    runtime_readiness_v2,
    runtime_status_v2,
    source_run_id_v2,
)
from la_heat.multicity.m3_source_development_worker import (
    _atomic_json,
    _Heartbeat,
    _utc_now,
)
from la_heat.multicity.m3_source_integrity_v2 import (
    authenticate_m3_source_integrity_v2_authorization,
)


class M3SourceWorkerV2Error(RuntimeError):
    """Raised when the v2 worker cannot preserve phase isolation."""


class TaskExecutor(Protocol):
    def execute(self, kind: str, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...


ExecutorFactory = Callable[[], TaskExecutor]


def _lock(handle: BinaryIO) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:  # pragma: no cover - production currently runs on Windows.
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(handle: BinaryIO) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:  # pragma: no cover - production currently runs on Windows.
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _exclusive_worker(path: Path) -> Iterator[None]:
    """Hold an OS-released lock so exactly one v2 compute worker can run."""

    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
    try:
        _lock(handle)
    except OSError as error:
        handle.close()
        raise M3SourceWorkerV2Error(
            "Another M3 integrity-v2 compute worker already owns the runtime lock."
        ) from error
    try:
        yield
    finally:
        try:
            _unlock(handle)
        finally:
            handle.close()


@dataclass(frozen=True, slots=True)
class WorkerOptionsV2:
    phase: str
    compute_workers: int = 1
    window_size: int = 512
    poll_seconds: float = 0.5

    def validate(self) -> None:
        if self.phase not in PHASES:
            raise ValueError(f"phase must be one of {PHASES}")
        if self.compute_workers != 1 or self.window_size != 512:
            raise ValueError("v2 fixes compute_workers=1 and window_size=512")
        if self.poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")


def _kind(queue: ModelRunQueue, run_id: str, kind: str) -> dict[str, int]:
    return dict(
        queue.counts_by_kind(run_id).get(
            kind,
            {"pending": 0, "running": 0, "complete": 0, "quarantined": 0, "total": 0},
        )
    )


def active_kind_v2(queue: ModelRunQueue, run_id: str, phase: str) -> str:
    sequences = {
        LOGICAL_PHASE: ("finalize_retained_scene", "finalize_logical_cache"),
        QA_PHASE: ("qa_overpass", "compile_qa_city", "finalize_qa_candidates"),
    }
    if phase not in sequences:
        raise M3SourceWorkerV2Error("Unknown v2 worker phase.")
    if phase == QA_PHASE and _kind(queue, run_id, "finalize_logical_cache")["complete"] != 1:
        raise M3SourceWorkerV2Error(
            "Offline QA is sealed until logical cache completion is durable."
        )
    for kind in sequences[phase]:
        counts = _kind(queue, run_id, kind)
        if counts["quarantined"]:
            raise M3SourceWorkerV2Error(f"Task kind {kind} contains quarantined work.")
        if counts["complete"] < counts["total"]:
            return kind
    return "complete"


def safe_worker_status_v2(
    queue: ModelRunQueue,
    run_id: str,
    *,
    settings: RunnerSettingsV2,
    phase: str,
    active_task_id: str | None = None,
    retry_count: int = 0,
    last_error_type: str | None = None,
) -> dict[str, Any]:
    payload = runtime_status_v2(queue, run_id, settings=settings)
    kinds = (
        ("finalize_retained_scene", "finalize_logical_cache")
        if phase == LOGICAL_PHASE
        else ("qa_overpass", "compile_qa_city", "finalize_qa_candidates")
    )
    try:
        active = active_kind_v2(queue, run_id, phase)
    except M3SourceWorkerV2Error:
        if payload["state"] != "failed":
            raise
        active = "failed"
    payload.update(
        {
            "active_phase": phase,
            "phase": active,
            "phase_complete": sum(_kind(queue, run_id, kind)["complete"] for kind in kinds),
            "phase_total": sum(_kind(queue, run_id, kind)["total"] for kind in kinds),
            "active_task_ids": [] if active_task_id is None else [active_task_id],
            "retry_count": retry_count,
            "last_error_type": last_error_type,
            "network_allowed": False,
            "href_reads_allowed": False,
            "network_request_count": 0,
            "updated_at_utc": _utc_now(),
        }
    )
    return payload


def _execute_phase_queue_unlocked_v2(
    *,
    settings: RunnerSettingsV2,
    run_id: str,
    options: WorkerOptionsV2,
    executor_factory: ExecutorFactory,
) -> dict[str, Any]:
    options.validate()
    queue = ModelRunQueue(settings.database)
    retry_count = 0
    last_error_type: str | None = None
    active_task_id: str | None = None

    def publish() -> dict[str, Any]:
        payload = safe_worker_status_v2(
            queue,
            run_id,
            settings=settings,
            phase=options.phase,
            active_task_id=active_task_id,
            retry_count=retry_count,
            last_error_type=last_error_type,
        )
        _atomic_json(settings.status, payload)
        return payload

    publish()
    local_queue = ModelRunQueue(settings.database)
    executor: TaskExecutor | None = None
    owner = f"m3-source-v2-{os.getpid()}-{uuid.uuid4().hex[:10]}"
    while True:
        if local_queue.get_desired_state(run_id) == "paused":
            break
        kind = active_kind_v2(local_queue, run_id, options.phase)
        if kind == "complete":
            break
        claim = local_queue.claim_next(
            run_id,
            owner=owner,
            lease_seconds=settings.lease_seconds,
            kinds={kind},
        )
        if claim is None:
            time.sleep(options.poll_seconds)
            continue
        active_task_id = claim.task_id
        publish()
        heartbeat = _Heartbeat(
            local_queue,
            claim,
            interval_seconds=settings.heartbeat_seconds,
            lease_seconds=settings.lease_seconds,
        )
        try:
            if executor is None:
                executor = executor_factory()
            with heartbeat:
                result = dict(executor.execute(kind, dict(claim.payload)))
            if heartbeat.lost.is_set():
                raise LeaseLostError("lease heartbeat was lost")
            local_queue.complete(
                run_id,
                claim.task_id,
                owner=owner,
                generation=claim.claim_generation,
                result=result,
            )
            last_error_type = None
        except LeaseLostError:
            last_error_type = "LeaseLostError"
        except Exception as error:
            error_type = type(error).__name__
            try:
                local_queue.retry(
                    run_id,
                    claim.task_id,
                    owner=owner,
                    generation=claim.claim_generation,
                    error_type=error_type,
                    base_delay_seconds=settings.retry_base_seconds,
                    max_delay_seconds=settings.retry_max_seconds,
                )
                retry_count += 1
                last_error_type = error_type
            except LeaseLostError:
                last_error_type = "LeaseLostError"
        finally:
            active_task_id = None
            publish()
    if active_kind_v2(queue, run_id, options.phase) == "complete":
        queue.set_desired_state(run_id, "paused")
    return publish()


def execute_phase_queue_v2(
    *,
    settings: RunnerSettingsV2,
    run_id: str,
    options: WorkerOptionsV2,
    executor_factory: ExecutorFactory,
) -> dict[str, Any]:
    lock_path = settings.control.with_suffix(".worker.lock")
    with _exclusive_worker(lock_path):
        return _execute_phase_queue_unlocked_v2(
            settings=settings,
            run_id=run_id,
            options=options,
            executor_factory=executor_factory,
        )


def prepare_worker_v2(
    project_root: str | Path,
) -> tuple[RunnerSettingsV2, str, dict[str, Any]]:
    settings = load_runner_settings_v2(project_root)
    readiness = runtime_readiness_v2(settings.root)
    if readiness["state"] != "ready_paused":
        _atomic_json(settings.status, readiness)
        return settings, "", readiness
    initialized = initialize_source_runtime_v2(settings.root)
    authorization = authenticate_m3_source_integrity_v2_authorization(
        settings.root, settings.authorization
    )
    run_id = source_run_id_v2(authorization)
    if initialized.get("run_id") != run_id:
        raise M3SourceWorkerV2Error("Initialized v2 runtime ID changed.")
    return settings, run_id, initialized
