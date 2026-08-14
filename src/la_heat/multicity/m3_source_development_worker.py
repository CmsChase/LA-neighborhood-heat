"""Low-load resumable worker for M3 source cache and offline QA phases."""

from __future__ import annotations

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
from la_heat.multicity.m3_source_development_runtime import (
    RunnerSettings,
    initialize_source_runtime,
    load_runner_settings,
    runtime_readiness,
    runtime_status,
)

ONLINE_PHASE: Final = "online_predownload"
OFFLINE_PHASE: Final = "offline_qa_rebuild"
PHASES: Final = (ONLINE_PHASE, OFFLINE_PHASE)


class M3SourceWorkerError(RuntimeError):
    """Raised when one worker cannot preserve phase isolation."""


class TaskExecutor(Protocol):
    def execute(self, kind: str, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...


ExecutorFactory = Callable[[], TaskExecutor]


@dataclass(frozen=True, slots=True)
class WorkerOptions:
    phase: str
    download_workers: int = 2
    compute_workers: int = 1
    window_size: int = 512
    poll_seconds: float = 0.5

    def validate(self) -> None:
        if self.phase not in PHASES:
            raise ValueError(f"phase must be one of {PHASES}")
        if self.download_workers not in (1, 2):
            raise ValueError("download_workers must be 1 or 2")
        if self.compute_workers != 1 or self.window_size != 512:
            raise ValueError("office mode fixes compute_workers=1 and window_size=512")
        if self.poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(dict(payload), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _kind(queue: ModelRunQueue, run_id: str, kind: str) -> dict[str, int]:
    return dict(
        queue.counts_by_kind(run_id).get(
            kind,
            {"pending": 0, "running": 0, "complete": 0, "quarantined": 0, "total": 0},
        )
    )


def active_kind(queue: ModelRunQueue, run_id: str, phase: str) -> str:
    """Return the only task kind that may be claimed in the current phase."""

    sequences = {
        ONLINE_PHASE: ("download_asset", "finalize_scene", "finalize_download"),
        OFFLINE_PHASE: ("qa_overpass", "compile_qa_city", "finalize_qa_candidates"),
    }
    if phase not in sequences:
        raise M3SourceWorkerError("Unknown worker phase.")
    if phase == OFFLINE_PHASE and _kind(queue, run_id, "finalize_download")["complete"] != 1:
        raise M3SourceWorkerError("Offline QA is sealed until the local cache is complete.")
    for kind in sequences[phase]:
        counts = _kind(queue, run_id, kind)
        if counts["quarantined"]:
            raise M3SourceWorkerError(f"Task kind {kind} contains quarantined work.")
        if counts["complete"] < counts["total"]:
            return kind
    return "complete"


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

    def __exit__(self, *_: object) -> None:
        self.stop.set()
        self.thread.join()


def safe_worker_status(
    queue: ModelRunQueue,
    run_id: str,
    *,
    settings: RunnerSettings,
    phase: str,
    active_task_ids: tuple[str, ...] = (),
    retry_count: int = 0,
    last_error_type: str | None = None,
    initial_completed: int = 0,
    elapsed_seconds: float = 0.0,
) -> dict[str, Any]:
    payload = runtime_status(queue, run_id, settings=settings)
    current_kind = active_kind(queue, run_id, phase)
    phase_kinds = (
        ("download_asset", "finalize_scene", "finalize_download")
        if phase == ONLINE_PHASE
        else ("qa_overpass", "compile_qa_city", "finalize_qa_candidates")
    )
    phase_complete = sum(_kind(queue, run_id, kind)["complete"] for kind in phase_kinds)
    phase_total = sum(_kind(queue, run_id, kind)["total"] for kind in phase_kinds)
    newly_complete = max(0, phase_complete - initial_completed)
    remaining = max(0, phase_total - phase_complete)
    eta = (
        0.0
        if remaining == 0
        else elapsed_seconds * remaining / newly_complete
        if newly_complete > 0
        else None
    )
    payload.update(
        {
            "active_phase": phase,
            "phase": current_kind,
            "phase_complete": phase_complete,
            "phase_total": phase_total,
            "active_task_ids": list(active_task_ids),
            "retry_count": retry_count,
            "last_error_type": last_error_type,
            "eta_seconds": eta,
            "network_allowed": phase == ONLINE_PHASE,
            "network_request_count_offline": 0,
            "updated_at_utc": _utc_now(),
        }
    )
    return payload


def execute_phase_queue(
    *,
    settings: RunnerSettings,
    run_id: str,
    options: WorkerOptions,
    executor_factory: ExecutorFactory,
) -> dict[str, Any]:
    options.validate()
    queue = ModelRunQueue(settings.database)
    workers = options.download_workers if options.phase == ONLINE_PHASE else 1
    state_lock = threading.Lock()
    active: set[str] = set()
    retry_count = 0
    last_error_type: str | None = None
    started = time.monotonic()
    kinds = (
        ("download_asset", "finalize_scene", "finalize_download")
        if options.phase == ONLINE_PHASE
        else ("qa_overpass", "compile_qa_city", "finalize_qa_candidates")
    )
    initial_completed = sum(_kind(queue, run_id, kind)["complete"] for kind in kinds)

    def publish() -> dict[str, Any]:
        with state_lock:
            payload = safe_worker_status(
                queue,
                run_id,
                settings=settings,
                phase=options.phase,
                active_task_ids=tuple(sorted(active)),
                retry_count=retry_count,
                last_error_type=last_error_type,
                initial_completed=initial_completed,
                elapsed_seconds=max(0.0, time.monotonic() - started),
            )
            _atomic_json(settings.status, payload)
            return payload

    publish()

    def worker(index: int) -> None:
        nonlocal retry_count, last_error_type
        local_queue = ModelRunQueue(settings.database)
        executor: TaskExecutor | None = None
        owner = f"m3-source-{os.getpid()}-{index}-{uuid.uuid4().hex[:10]}"
        while True:
            if local_queue.get_desired_state(run_id) == "paused":
                return
            kind = active_kind(local_queue, run_id, options.phase)
            if kind == "complete":
                return
            claim = local_queue.claim_next(
                run_id,
                owner=owner,
                lease_seconds=settings.lease_seconds,
                kinds={kind},
            )
            if claim is None:
                time.sleep(options.poll_seconds)
                continue
            with state_lock:
                active.add(claim.task_id)
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
                with state_lock:
                    last_error_type = None
            except LeaseLostError:
                with state_lock:
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
                    with state_lock:
                        retry_count += 1
                        last_error_type = error_type
                except LeaseLostError:
                    with state_lock:
                        last_error_type = "LeaseLostError"
            finally:
                with state_lock:
                    active.discard(claim.task_id)
                publish()

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="m3-source") as pool:
        futures = [pool.submit(worker, index) for index in range(workers)]
        for future in futures:
            future.result()
    if active_kind(queue, run_id, options.phase) == "complete":
        queue.set_desired_state(run_id, "paused")
    return publish()


def prepare_worker(project_root: str | Path) -> tuple[RunnerSettings, str, dict[str, Any]]:
    settings = load_runner_settings(project_root)
    readiness = runtime_readiness(settings.root)
    if readiness["state"] != "ready_paused":
        _atomic_json(settings.status, readiness)
        return settings, "", readiness
    initialized = initialize_source_runtime(settings.root)
    inventory_commit = str(readiness["inventory_commit_sha256"])
    run_id = f"m3-source-development-v1-{inventory_commit[:16]}"
    if initialized.get("run_id") != run_id:
        raise M3SourceWorkerError("Initialized runtime ID changed.")
    return settings, run_id, initialized
