"""Local start/pause dashboard for the resumable Sentinel-2 acquisition build.

This module is an orchestration layer only. It calls the already fingerprinted
per-acquisition processor and compiler without changing their scientific
semantics or cache locks. Pause requests are cooperative: no new acquisition is
started, and any active acquisition is allowed to commit atomically first.
"""

from __future__ import annotations

import argparse
import heapq
import json
import math
import os
import secrets
import statistics
import threading
import time
import webbrowser
from collections import deque
from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from pystac_client.exceptions import APIError
from rasterio.errors import RasterioIOError

from la_heat.config import load_config
from la_heat.provenance import (
    atomic_json,
    canonical_sha256,
    code_runtime_fingerprint,
    sha256_file,
)
from la_heat.sentinel_compile_adapter import compile_outputs_from_current_caches
from la_heat.sentinel_feature_builder import (
    FixedSpatialSupport,
    FrozenSentinelInputs,
    SentinelStageConfig,
    _acquisition_cache_directory,
    _acquisition_cache_is_current,
    _expected_acquisition_lock,
    _load_fixed_spatial_support,
    _load_frozen_sentinel_inventory,
    _pipeline_fingerprint,
    _process_acquisition,
    _research_dependency_payload,
    _resolve_project_path,
    load_sentinel_stage_config,
)

RUNNER_VERSION = "sentinel-dashboard-orchestrator-v3"
RUNNER_FILES = (
    "scripts/sentinel_dashboard.py",
    "scripts/sentinel_dashboard_watchdog.py",
    "src/la_heat/sentinel_compile_adapter.py",
    "src/la_heat/sentinel_dashboard.py",
    "src/la_heat/sentinel_dashboard_watchdog.py",
    "tools/sentinel_dashboard/index.html",
)
FINAL_STATES = frozenset({"paused", "complete", "error"})
DEFAULT_ACQUISITION_RETRY_DELAYS = (15.0, 60.0, 180.0, 600.0)


def _is_retryable_acquisition_error(exc: BaseException) -> bool:
    """Return whether an acquisition failure is plausibly transient.

    Exception messages are intentionally ignored because remote raster URLs can
    contain short-lived credentials. Integrity, schema, and configuration
    failures (for example ``ValueError``) remain fail-closed.
    """

    return isinstance(
        exc,
        (
            APIError,
            requests.RequestException,
            RasterioIOError,
            ConnectionError,
            TimeoutError,
        ),
    )


class TransientSpatialSupportError(RuntimeError):
    """Remote STAC/COG support could not be revalidated yet."""


class DashboardProcessLock:
    """An OS-released lock preventing two dashboards from writing one cache tree."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._stream: Any = None

    def __enter__(self) -> DashboardProcessLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+b")
        if self.path.stat().st_size == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            stream.close()
            raise RuntimeError(
                "Another Sentinel dashboard already holds the acquisition-cache lock."
            ) from exc
        self._stream = stream
        return self

    def __exit__(self, *_args: object) -> None:
        stream = self._stream
        if stream is None:
            return
        try:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()
            self._stream = None


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class AcquisitionJob:
    """One unique frozen physical acquisition and its selected tile rows."""

    physical_id: str
    acquisition_row: Any
    item_rows: pd.DataFrame


@dataclass(frozen=True, slots=True)
class DashboardContext:
    """Read-only scientific context plus audit-only runner provenance."""

    project_root: Path
    research: Any
    stage: SentinelStageConfig
    inventory: FrozenSentinelInputs
    spatial: FixedSpatialSupport
    output_directory: Path
    raw_metadata_directory: Path
    base_lock: dict[str, str]
    pipeline_payload: dict[str, Any]
    runner_sha256: str
    runner_payload: dict[str, Any]
    current_ids: tuple[str, ...]
    jobs: tuple[AcquisitionJob, ...]
    historical_seconds_per_acquisition: float | None


class CooperativeAcquisitionRunner:
    """Run bounded jobs and honor pause only between atomic jobs."""

    def __init__(
        self,
        *,
        jobs: Sequence[AcquisitionJob],
        all_ids: Sequence[str],
        initial_completed_ids: Sequence[str],
        worker: Callable[[AcquisitionJob], dict[str, Any]],
        checkpoint: Callable[[], dict[str, Any]],
        progress_hook: Callable[[dict[str, Any]], None] | None = None,
        workers: int = 1,
        historical_seconds_per_job: float | None = None,
        retry_delays: Sequence[float] = DEFAULT_ACQUISITION_RETRY_DELAYS,
        retryable_error: Callable[[BaseException], bool] = (
            _is_retryable_acquisition_error
        ),
    ) -> None:
        if workers not in {1, 2}:
            raise ValueError("Dashboard workers must be 1 or 2.")
        ordered_ids = tuple(str(value) for value in all_ids)
        if not ordered_ids or len(set(ordered_ids)) != len(ordered_ids):
            raise ValueError("All acquisition IDs must be non-empty and unique.")
        pending_ids = [job.physical_id for job in jobs]
        if len(set(pending_ids)) != len(pending_ids):
            raise ValueError("Pending acquisition jobs must be unique.")
        completed = {str(value) for value in initial_completed_ids}
        if completed & set(pending_ids):
            raise ValueError("Completed and pending acquisition IDs overlap.")
        if completed | set(pending_ids) != set(ordered_ids):
            raise ValueError("Completed and pending IDs do not cover the frozen inventory.")
        retry_schedule = tuple(float(value) for value in retry_delays)
        if any(not math.isfinite(value) or value <= 0 for value in retry_schedule):
            raise ValueError("Acquisition retry delays must be finite and positive.")

        self._jobs = deque(jobs)
        self._delayed_jobs: list[tuple[float, int, AcquisitionJob]] = []
        self._quarantined_jobs: deque[AcquisitionJob] = deque()
        self._quarantined_errors: dict[str, dict[str, Any]] = {}
        self._all_ids = ordered_ids
        self._completed_ids = completed
        self._worker = worker
        self._checkpoint = checkpoint
        self._progress_hook = progress_hook
        self._workers = workers
        self._retry_delays = retry_schedule
        self._retryable_error = retryable_error
        self._failure_counts: dict[str, int] = {}
        self._retry_attempts_total = 0
        self._retry_sequence = 0
        self._last_failure: dict[str, Any] | None = None
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._state = "idle"
        self._pause_requested = False
        self._active: dict[Future[dict[str, Any]], tuple[AcquisitionJob, float, str]] = {}
        self._durations: deque[float] = deque(maxlen=30)
        if historical_seconds_per_job is not None and historical_seconds_per_job > 0:
            self._durations.append(float(historical_seconds_per_job))
        self._events: deque[dict[str, str]] = deque(maxlen=80)
        self._error: dict[str, Any] | None = None
        self._last_checkpoint: dict[str, Any] | None = None
        self._coordinator: threading.Thread | None = None
        self._started_at: str | None = None
        self._closed = False
        self._add_event(
            f"发现 {len(self._completed_ids)} 个已验证缓存；"
            f"剩余 {len(self._jobs)} 个 acquisition。"
        )

    def _add_event(self, message: str) -> None:
        with self._lock:
            self._events.append({"at": _utc_now(), "message": message})

    def _ordered_completed_ids(self) -> list[str]:
        return [value for value in self._all_ids if value in self._completed_ids]

    def _snapshot_locked(self) -> dict[str, Any]:
        now = time.monotonic()
        active = [
            {
                "physical_acquisition_id": job.physical_id,
                "started_at_utc": started_at,
                "elapsed_seconds": round(max(0.0, now - started_monotonic), 1),
            }
            for job, started_monotonic, started_at in self._active.values()
        ]
        duration = statistics.fmean(self._durations) if self._durations else None
        remaining = len(self._all_ids) - len(self._completed_ids)
        eta = None
        if duration is not None and self._state not in {"paused", "error", "complete"}:
            eta = duration * remaining / self._workers
        completed = len(self._completed_ids)
        retrying = [
            {
                "physical_acquisition_id": job.physical_id,
                "failed_attempts": self._failure_counts.get(job.physical_id, 0),
                "retry_in_seconds": round(max(0.0, ready_at - now), 1),
            }
            for ready_at, _, job in sorted(self._delayed_jobs)
        ]
        return {
            "state": self._state,
            "pause_requested": self._pause_requested,
            "workers": self._workers,
            "completed": completed,
            "total": len(self._all_ids),
            "pending": (
                len(self._jobs)
                + len(self._delayed_jobs)
                + len(self._quarantined_jobs)
            ),
            "active": active,
            "progress_fraction": completed / len(self._all_ids),
            "mean_seconds_per_acquisition": None if duration is None else round(duration, 1),
            "eta_seconds": None if eta is None else round(eta),
            "started_at_utc": self._started_at,
            "error": self._error,
            "last_failure": self._last_failure,
            "retry_attempts_total": self._retry_attempts_total,
            "retrying_count": len(retrying),
            "retrying": retrying,
            "quarantined_count": len(self._quarantined_jobs),
            "events": list(self._events)[-20:],
            "last_checkpoint_state": (
                None if self._last_checkpoint is None else self._last_checkpoint.get("state")
            ),
            "completed_ids_sha256": canonical_sha256(self._ordered_completed_ids()),
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._snapshot_locked()

    def _publish_progress(self) -> None:
        if self._progress_hook is not None:
            try:
                self._progress_hook(self.snapshot())
            except Exception as exc:  # noqa: BLE001 - audit-only hook, sanitized
                self._add_event(
                    f"进度摘要写入失败（{type(exc).__name__}）；"
                    "acquisition 缓存处理继续。"
                )

    def start_or_resume(self) -> dict[str, Any]:
        with self._condition:
            if self._closed:
                return self._snapshot_locked()
            if self._state == "complete":
                return self._snapshot_locked()
            if self._state in {"running", "pausing", "compiling"}:
                return self._snapshot_locked()
            if self._state == "error" and self._quarantined_jobs:
                while self._quarantined_jobs:
                    job = self._quarantined_jobs.popleft()
                    self._failure_counts.pop(job.physical_id, None)
                    self._quarantined_errors.pop(job.physical_id, None)
                    self._jobs.append(job)
            self._pause_requested = False
            self._error = None
            self._state = "running"
            if self._started_at is None:
                self._started_at = _utc_now()
            self._add_event("构建已开始或继续。")
            if self._coordinator is None or not self._coordinator.is_alive():
                self._coordinator = threading.Thread(
                    target=self._coordinate,
                    name="sentinel-dashboard-coordinator",
                    daemon=True,
                )
                self._coordinator.start()
            self._condition.notify_all()
            snapshot = self._snapshot_locked()
        self._publish_progress()
        return snapshot

    def request_pause(self) -> dict[str, Any]:
        with self._condition:
            if self._state == "idle":
                self._pause_requested = True
                self._state = "paused"
                self._add_event("尚未启动；当前已保持暂停。")
                self._condition.notify_all()
            elif self._state == "running":
                self._pause_requested = True
                self._state = "pausing"
                self._add_event("收到安全暂停请求；等待当前 acquisition 完整落盘。")
                self._condition.notify_all()
            return self._snapshot_locked()

    def wait_for_final_state(self, timeout: float | None = None) -> str:
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while self._state not in FINAL_STATES:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    break
                self._condition.wait(timeout=remaining)
            return self._state

    def shutdown(self) -> None:
        """Safely stop at an acquisition boundary and release worker threads."""

        self.request_pause()
        self.wait_for_final_state(timeout=None)
        with self._condition:
            self._closed = True
            self._condition.notify_all()
            coordinator = self._coordinator
        if coordinator is not None and coordinator is not threading.current_thread():
            coordinator.join()

    def _next_ready_job_locked(self) -> AcquisitionJob | None:
        now = time.monotonic()
        if self._delayed_jobs and self._delayed_jobs[0][0] <= now:
            _, _, job = heapq.heappop(self._delayed_jobs)
            return job
        if self._jobs:
            return self._jobs.popleft()
        return None

    def _seconds_until_retry_locked(self) -> float | None:
        if not self._delayed_jobs:
            return None
        return max(0.0, self._delayed_jobs[0][0] - time.monotonic())

    def _submit_available(self, executor: ThreadPoolExecutor) -> None:
        while not self._pause_requested and len(self._active) < self._workers:
            job = self._next_ready_job_locked()
            if job is None:
                break
            started_at = _utc_now()
            future = executor.submit(self._worker, job)
            self._active[future] = (job, time.monotonic(), started_at)
            self._add_event(f"开始 {job.physical_id}")

    def _handle_finished(self, futures: set[Future[dict[str, Any]]]) -> None:
        for future in futures:
            with self._condition:
                job, started_monotonic, _ = self._active.pop(future)
            try:
                future.result()
            except Exception as exc:  # noqa: BLE001 - sanitized at the process boundary
                with self._condition:
                    retryable = bool(self._retryable_error(exc))
                    failed_attempts = self._failure_counts.get(job.physical_id, 0) + 1
                    self._failure_counts[job.physical_id] = failed_attempts
                    self._retry_attempts_total += 1
                    self._last_failure = {
                        "physical_acquisition_id": job.physical_id,
                        "type": type(exc).__name__,
                        "retryable": retryable,
                        "failed_attempts": failed_attempts,
                    }
                    if retryable and failed_attempts <= len(self._retry_delays):
                        delay = self._retry_delays[failed_attempts - 1]
                        self._retry_sequence += 1
                        heapq.heappush(
                            self._delayed_jobs,
                            (time.monotonic() + delay, self._retry_sequence, job),
                        )
                        self._add_event(
                            f"{job.physical_id} 失败（{type(exc).__name__}）；"
                            f"{delay:.0f} 秒后自动进行第 {failed_attempts + 1} 次尝试。"
                        )
                    else:
                        self._quarantined_jobs.append(job)
                        self._quarantined_errors[job.physical_id] = dict(
                            self._last_failure
                        )
                        reason = "自动重试已耗尽" if retryable else "非瞬时错误"
                        self._add_event(
                            f"{job.physical_id} 失败（{type(exc).__name__}，{reason}）；"
                            "其余 acquisition 将继续，随后自动恢复整个批次。"
                        )
                    self._condition.notify_all()
            else:
                duration = max(0.0, time.monotonic() - started_monotonic)
                with self._condition:
                    self._completed_ids.add(job.physical_id)
                    self._failure_counts.pop(job.physical_id, None)
                    self._durations.append(duration)
                    self._add_event(
                        f"完成 {job.physical_id}，耗时 {duration / 60:.1f} 分钟。"
                    )
                    self._condition.notify_all()
            self._publish_progress()

    def _run_checkpoint(self, *, final: bool) -> bool:
        with self._condition:
            self._state = "compiling"
            self._add_event("正在编译最终特征。" if final else "正在写入可恢复检查点。")
        try:
            result = self._checkpoint()
        except Exception as exc:  # noqa: BLE001 - never expose signed remote URLs
            with self._condition:
                self._error = {
                    "physical_acquisition_id": "compile",
                    "type": type(exc).__name__,
                    "retryable": _is_retryable_acquisition_error(exc),
                }
                self._state = "error"
                self._add_event(f"编译失败（{type(exc).__name__}）。")
                self._condition.notify_all()
            return False
        with self._condition:
            self._last_checkpoint = result
            if final:
                if result.get("state") != "complete" or result.get(
                    "promoted_outputs_valid"
                ) is not True:
                    self._error = {
                        "physical_acquisition_id": "compile",
                        "type": "IncompletePromotion",
                        "retryable": False,
                    }
                    self._state = "error"
                    self._add_event("最终编译未产生完整 promoted outputs。")
                    self._condition.notify_all()
                    return False
                self._state = "complete"
                self._add_event("226/226 完成；60 日特征与 lineage 已原子 promoted。")
            elif self._error is not None:
                self._state = "error"
            elif self._pause_requested:
                self._state = "paused"
                self._add_event("已在 acquisition 边界安全暂停。")
            else:
                self._state = "running"
            self._condition.notify_all()
        return True

    def _coordinate(self) -> None:
        executor = ThreadPoolExecutor(
            max_workers=self._workers,
            thread_name_prefix="sentinel-acquisition",
        )
        try:
            while True:
                action: str | None = None
                active_futures: set[Future[dict[str, Any]]] = set()
                with self._condition:
                    if self._closed:
                        return
                    if self._state == "running":
                        self._submit_available(executor)
                    active_futures = set(self._active)
                    if not active_futures:
                        if self._pause_requested:
                            action = "pause"
                        elif not self._jobs and not self._delayed_jobs:
                            action = (
                                "error" if self._quarantined_jobs else "final"
                            )
                        elif self._state == "paused":
                            self._condition.wait(timeout=0.5)
                            continue
                    if action is None and not active_futures:
                        retry_wait = self._seconds_until_retry_locked()
                        self._condition.wait(timeout=retry_wait)
                        continue

                if action == "pause":
                    self._run_checkpoint(final=False)
                    with self._condition:
                        while self._state in {"paused", "error"} and not self._closed:
                            self._condition.wait(timeout=0.5)
                            if self._state == "running":
                                break
                        if self._closed:
                            return
                    continue
                if action == "final":
                    self._run_checkpoint(final=True)
                    return
                if action == "error":
                    with self._condition:
                        first_quarantined = self._quarantined_jobs[0]
                        failure = dict(
                            self._quarantined_errors[first_quarantined.physical_id]
                        )
                        failure.setdefault(
                            "physical_acquisition_id", "acquisition"
                        )
                        failure.setdefault("type", "AcquisitionFailure")
                        failure["retryable"] = all(
                            item.get("retryable") is True
                            for item in self._quarantined_errors.values()
                        )
                        failure["quarantined_count"] = len(
                            self._quarantined_jobs
                        )
                        self._error = failure
                        self._state = "error"
                        self._add_event(
                            "可执行 acquisition 已处理完；失败项等待自动重建批次后重试。"
                        )
                        self._condition.notify_all()
                    self._publish_progress()
                    return

                done, _ = wait(active_futures, timeout=0.5, return_when=FIRST_COMPLETED)
                if done:
                    self._handle_finished(done)
        except Exception as exc:  # noqa: BLE001 - fail closed at thread boundary
            with self._condition:
                self._error = {
                    "physical_acquisition_id": "coordinator",
                    "type": type(exc).__name__,
                    "retryable": True,
                }
                self._state = "error"
                self._closed = True
                self._add_event(
                    f"协调线程失败（{type(exc).__name__}）；将自动重建批次。"
                )
                self._condition.notify_all()
        finally:
            executor.shutdown(wait=True, cancel_futures=True)


class LazyDashboardRunner:
    """Keep the UI available while transient remote support validation retries."""

    def __init__(
        self,
        *,
        research_config_path: str | Path,
        stage_config_path: str | Path,
        workers: int,
        retry_seconds: float = 20.0,
        supervisor_poll_seconds: float = 0.25,
    ) -> None:
        if workers not in {1, 2}:
            raise ValueError("Dashboard workers must be 1 or 2.")
        if not math.isfinite(retry_seconds) or retry_seconds <= 0:
            raise ValueError("Dashboard retry seconds must be finite and positive.")
        if not math.isfinite(supervisor_poll_seconds) or supervisor_poll_seconds <= 0:
            raise ValueError("Supervisor poll seconds must be finite and positive.")
        self._research_config_path = Path(research_config_path)
        self._stage_config_path = Path(stage_config_path)
        self._workers = workers
        self._retry_seconds = retry_seconds
        self._supervisor_poll_seconds = supervisor_poll_seconds
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._delegate: CooperativeAcquisitionRunner | None = None
        self._initializer: threading.Thread | None = None
        self._supervisor: threading.Thread | None = None
        self._state = "initializing"
        self._desired_running = False
        self._auto_start = False
        self._automatic_restart_count = 0
        self._error: dict[str, Any] | None = None
        self._events: deque[dict[str, str]] = deque(maxlen=30)

        project_root = Path(__file__).resolve().parents[2]
        stage = load_sentinel_stage_config(self._stage_config_path)
        paths = stage.raw["paths"]
        inventory_directory = _resolve_project_path(
            project_root, paths["inventory_directory"]
        )
        acquisition_inventory = pd.read_csv(
            inventory_directory / "selected_acquisitions.csv"
        )
        if (
            "physical_acquisition_id" not in acquisition_inventory.columns
            or acquisition_inventory.empty
            or acquisition_inventory["physical_acquisition_id"].duplicated().any()
        ):
            raise ValueError("Frozen acquisition inventory must contain unique IDs.")
        self._total = len(acquisition_inventory)
        output_directory = _resolve_project_path(project_root, paths["output_directory"])
        self._control_path = output_directory / "dashboard_control.json"
        self._cache_markers = min(
            self._total,
            len(list((output_directory / "by_acquisition").glob("*/summary.json"))),
        )
        self._restore_control_intent()
        self._add_event(
            f"面板已启动；发现 {self._cache_markers} 个本地 completion markers，"
            "正在重新验证固定 support 与 cache locks。"
        )

    def _add_event(self, message: str) -> None:
        with self._lock:
            self._events.append({"at": _utc_now(), "message": message})

    def _restore_control_intent(self) -> None:
        if not self._control_path.exists():
            return
        try:
            payload = json.loads(self._control_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._add_event("控制意图文件不可读；为安全起见保持未启动。")
            return
        if payload.get("desired_state") == "running":
            self._desired_running = True
            self._auto_start = True
            self._add_event("已恢复上次的运行意图；验证缓存后将自动继续。")

    def _write_control_intent_locked(self, desired_state: str) -> None:
        if desired_state not in {"running", "paused", "complete", "error"}:
            raise ValueError("Invalid dashboard desired state.")
        atomic_json(
            {
                "schema_version": 1,
                "desired_state": desired_state,
                "workers_audit_only": self._workers,
                "runner_version_audit_only": RUNNER_VERSION,
                "updated_at_utc_audit_only": _utc_now(),
            },
            self._control_path,
        )

    def _ensure_supervisor_locked(self) -> None:
        if self._supervisor is not None and self._supervisor.is_alive():
            return
        self._supervisor = threading.Thread(
            target=self._supervise_loop,
            name="sentinel-dashboard-supervisor",
            daemon=True,
        )
        self._supervisor.start()

    def begin_initialization(self) -> None:
        with self._lock:
            if self._stop.is_set():
                return
            self._ensure_supervisor_locked()
            if self._initializer is not None and self._initializer.is_alive():
                return
            self._state = "initializing"
            self._error = None
            self._initializer = threading.Thread(
                target=self._initialize_loop,
                name="sentinel-dashboard-initializer",
                daemon=True,
            )
            self._initializer.start()

    def _initialize_loop(self) -> None:
        while not self._stop.is_set():
            try:
                context = load_dashboard_context(
                    self._research_config_path,
                    self._stage_config_path,
                    spatial_attempts=1,
                )
                delegate = build_dashboard_runner(context, workers=self._workers)
            except TransientSpatialSupportError as exc:
                self._add_event(
                    f"远程固定 support 暂不可用（{type(exc).__name__}）；"
                    f"{self._retry_seconds:.0f} 秒后自动重试。"
                )
                self._stop.wait(self._retry_seconds)
                continue
            except Exception as exc:  # noqa: BLE001 - store type only, never URL text
                retryable = _is_retryable_acquisition_error(exc)
                with self._lock:
                    should_retry = retryable and self._desired_running
                    self._error = {
                        "physical_acquisition_id": "initialization",
                        "type": type(exc).__name__,
                        "retryable": retryable,
                    }
                    if should_retry:
                        self._state = "restarting"
                        self._add_event(
                            f"初始化暂时失败（{type(exc).__name__}）；"
                            f"{self._retry_seconds:.0f} 秒后自动重试。"
                        )
                    else:
                        self._initializer = None
                        self._state = "error"
                        self._auto_start = False
                        if self._desired_running:
                            self._desired_running = False
                            self._write_control_intent_locked("error")
                        self._add_event(
                            f"本地锁或配置验证失败（{type(exc).__name__}）；"
                            "为保护科学完整性，需修复后再继续。"
                        )
                if should_retry:
                    self._stop.wait(self._retry_seconds)
                    continue
                return

            with self._lock:
                if self._stop.is_set():
                    self._initializer = None
                    return
                self._delegate = delegate
                self._initializer = None
                self._state = "idle"
                self._error = None
                self._cache_markers = len(context.current_ids)
                self._add_event(
                    f"严格验证完成：{len(context.current_ids)}/{self._total} 个缓存有效。"
                )
                if self._desired_running:
                    self._auto_start = False
                    delegate.start_or_resume()
            return

    def _supervise_loop(self) -> None:
        while not self._stop.wait(self._supervisor_poll_seconds):
            with self._lock:
                delegate = self._delegate
                desired_running = self._desired_running
            if delegate is None:
                continue

            snapshot = delegate.snapshot()
            state = snapshot.get("state")
            if state == "complete":
                with self._lock:
                    if self._delegate is delegate and self._desired_running:
                        self._desired_running = False
                        self._auto_start = False
                        self._write_control_intent_locked("complete")
                        self._add_event("批处理完成；自动恢复意图已关闭。")
                continue
            if state != "error" or not desired_running:
                continue

            error = snapshot.get("error") or {}
            if error.get("retryable") is not True:
                with self._lock:
                    if self._delegate is delegate and self._desired_running:
                        self._desired_running = False
                        self._auto_start = False
                        self._error = dict(error)
                        self._write_control_intent_locked("error")
                        self._add_event(
                            "检测到科学完整性或配置错误；未自动循环重试。"
                        )
                continue

            with self._lock:
                if self._delegate is not delegate or not self._desired_running:
                    continue
                self._delegate = None
                self._state = "restarting"
                self._error = dict(error)
                self._cache_markers = int(snapshot.get("completed", 0))
                self._automatic_restart_count += 1
                self._auto_start = True
                delay = min(
                    self._retry_seconds
                    * (2 ** min(self._automatic_restart_count - 1, 4)),
                    300.0,
                )
                self._add_event(
                    f"批次发生可恢复错误（{error.get('type', 'Error')}）；"
                    f"{delay:.0f} 秒后自动重建并继续。"
                )

            delegate.shutdown()
            if self._stop.wait(delay):
                return
            with self._lock:
                should_initialize = (
                    self._desired_running
                    and self._delegate is None
                    and not self._stop.is_set()
                )
            if should_initialize:
                self.begin_initialization()

    def _initial_snapshot(self) -> dict[str, Any]:
        with self._lock:
            completed = self._cache_markers
            return {
                "state": self._state,
                "pause_requested": False,
                "start_queued": self._auto_start,
                "verification_pending": self._delegate is None,
                "workers": self._workers,
                "completed": completed,
                "total": self._total,
                "pending": self._total - completed,
                "active": [],
                "progress_fraction": completed / self._total,
                "mean_seconds_per_acquisition": None,
                "eta_seconds": None,
                "started_at_utc": None,
                "error": self._error,
                "last_failure": None,
                "retry_attempts_total": 0,
                "retrying_count": 0,
                "retrying": [],
                "quarantined_count": 0,
                "auto_restart_enabled": self._desired_running,
                "automatic_restart_count": self._automatic_restart_count,
                "events": list(self._events)[-20:],
                "last_checkpoint_state": None,
                "completed_ids_sha256": None,
            }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            delegate = self._delegate
            initial_events = list(self._events)
            desired_running = self._desired_running
            automatic_restart_count = self._automatic_restart_count
        if delegate is None:
            return self._initial_snapshot()
        snapshot = delegate.snapshot()
        snapshot["start_queued"] = False
        snapshot["verification_pending"] = False
        snapshot["auto_restart_enabled"] = desired_running
        snapshot["automatic_restart_count"] = automatic_restart_count
        snapshot["events"] = (initial_events + snapshot["events"])[-20:]
        return snapshot

    def start_or_resume(self) -> dict[str, Any]:
        with self._lock:
            self._write_control_intent_locked("running")
            self._desired_running = True
            delegate = self._delegate
            if delegate is None:
                self._auto_start = True
                if self._state == "error":
                    self._add_event("重新开始初始化与严格锁验证。")
                    self.begin_initialization()
                else:
                    self._add_event("已排队：固定 support 验证成功后自动开始。")
                return self._initial_snapshot()
        return delegate.start_or_resume()

    def request_pause(self) -> dict[str, Any]:
        with self._lock:
            self._write_control_intent_locked("paused")
            self._desired_running = False
            delegate = self._delegate
            if delegate is None:
                was_queued = self._auto_start
                self._auto_start = False
                if was_queued:
                    self._add_event("已取消自动开始；support 验证仍会在后台重试。")
                return self._initial_snapshot()
        return delegate.request_pause()

    def wait_for_final_state(self, timeout: float | None = None) -> str:
        with self._lock:
            delegate = self._delegate
        if delegate is None:
            return "paused"
        return delegate.wait_for_final_state(timeout=timeout)

    def shutdown(self) -> None:
        with self._lock:
            self._stop.set()
            delegate = self._delegate
            initializer = self._initializer
            supervisor = self._supervisor
        if delegate is not None:
            delegate.shutdown()
        if (
            initializer is not None
            and initializer is not threading.current_thread()
            and initializer.is_alive()
        ):
            initializer.join(timeout=5)
        if (
            supervisor is not None
            and supervisor is not threading.current_thread()
            and supervisor.is_alive()
        ):
            supervisor.join(timeout=5)


def _historical_duration_seconds(summary_paths: Sequence[Path]) -> float | None:
    timestamps = sorted(path.stat().st_mtime for path in summary_paths)
    differences = [
        later - earlier
        for earlier, later in zip(timestamps, timestamps[1:], strict=False)
    ]
    plausible = [value for value in differences if 30 <= value <= 1_200]
    return statistics.median(plausible) if plausible else None


def _load_spatial_support_with_retry(
    *,
    project_root: Path,
    research: Any,
    stage: SentinelStageConfig,
    inventory: FrozenSentinelInputs,
    attempts: int = 5,
) -> FixedSpatialSupport:
    """Retry only transient remote STAC/COG failures; keep every hash check."""

    if attempts < 1:
        raise ValueError("Spatial-support attempts must be positive.")
    retryable = (APIError, requests.RequestException, RasterioIOError)
    for attempt in range(1, attempts + 1):
        try:
            return _load_fixed_spatial_support(
                project_root=project_root,
                research=research,
                stage=stage,
                inventory=inventory,
            )
        except retryable as exc:
            if attempt == attempts:
                raise TransientSpatialSupportError(
                    "Fixed spatial support could not be revalidated after transient "
                    f"remote failures ({type(exc).__name__})."
                ) from None
            delay = min(2 ** attempt, 16)
            print(
                f"Spatial-support attempt {attempt}/{attempts} failed "
                f"({type(exc).__name__}); retrying in {delay}s.",
                flush=True,
            )
            time.sleep(delay)
    raise AssertionError("Unreachable spatial-support retry state.")


def load_dashboard_context(
    research_config_path: str | Path,
    stage_config_path: str | Path,
    *,
    spatial_attempts: int = 5,
) -> DashboardContext:
    """Load frozen inputs and preserve the existing scientific pipeline SHA."""

    project_root = Path(__file__).resolve().parents[2]
    research = load_config(research_config_path)
    stage = load_sentinel_stage_config(stage_config_path)
    paths = stage.raw["paths"]
    inventory = _load_frozen_sentinel_inventory(
        _resolve_project_path(project_root, paths["inventory_directory"]),
        research=research,
    )
    spatial = _load_spatial_support_with_retry(
        project_root=project_root,
        research=research,
        stage=stage,
        inventory=inventory,
        attempts=spatial_attempts,
    )
    spatial.zones.flags.writeable = False
    spatial.eligible_land.flags.writeable = False
    output_directory = _resolve_project_path(project_root, paths["output_directory"])
    output_directory.mkdir(parents=True, exist_ok=True)
    pipeline_sha, pipeline_payload = _pipeline_fingerprint(project_root)
    fingerprint_path = output_directory / "pipeline_fingerprint.json"
    atomic_json(pipeline_payload, fingerprint_path)
    base_lock = {
        "sentinel_feature_pipeline_sha256": pipeline_sha,
        "sentinel_feature_pipeline_fingerprint_file_sha256": sha256_file(
            fingerprint_path
        ),
        "sentinel_stage_config_sha256": stage.sha256,
        "sentinel_research_dependency_sha256": canonical_sha256(
            _research_dependency_payload(research)
        ),
        "research_config_file_sha256_audit_only": sha256_file(research.path),
        **inventory.locks,
        **spatial.locks,
    }
    runner_sha, runner_payload = code_runtime_fingerprint(
        project_root=project_root,
        relative_paths=RUNNER_FILES,
        algorithm_version=RUNNER_VERSION,
    )
    atomic_json(runner_payload, output_directory / "dashboard_runner_fingerprint.json")

    current_ids: list[str] = []
    jobs: list[AcquisitionJob] = []
    cache_paths: set[Path] = set()
    summary_paths: list[Path] = []
    for row in inventory.acquisitions.itertuples(index=False):
        physical_id = str(row.physical_acquisition_id)
        item_rows = inventory.items.loc[
            inventory.items["physical_acquisition_id"] == physical_id
        ].copy()
        directory = _acquisition_cache_directory(output_directory, physical_id)
        if directory in cache_paths:
            raise ValueError("Two frozen acquisitions resolve to the same cache directory.")
        cache_paths.add(directory)
        expected = _expected_acquisition_lock(
            base_lock=base_lock,
            physical_id=physical_id,
            item_rows=item_rows,
        )
        if _acquisition_cache_is_current(directory, expected_lock=expected):
            current_ids.append(physical_id)
            summary_paths.append(directory / "summary.json")
        else:
            jobs.append(AcquisitionJob(physical_id, row, item_rows))

    return DashboardContext(
        project_root=project_root,
        research=research,
        stage=stage,
        inventory=inventory,
        spatial=spatial,
        output_directory=output_directory,
        raw_metadata_directory=project_root / "data/raw/sentinel/product_metadata",
        base_lock=base_lock,
        pipeline_payload=pipeline_payload,
        runner_sha256=runner_sha,
        runner_payload=runner_payload,
        current_ids=tuple(current_ids),
        jobs=tuple(jobs),
        historical_seconds_per_acquisition=_historical_duration_seconds(summary_paths),
    )


def build_dashboard_runner(
    context: DashboardContext, *, workers: int
) -> CooperativeAcquisitionRunner:
    """Bind the generic cooperative runner to the frozen Sentinel stage."""

    def process_one(job: AcquisitionJob) -> dict[str, Any]:
        with requests.Session() as session:
            result = _process_acquisition(
                job.acquisition_row,
                item_rows=job.item_rows,
                spatial=context.spatial,
                stage=context.stage,
                base_lock=context.base_lock,
                output_directory=context.output_directory,
                raw_metadata_directory=context.raw_metadata_directory,
                session=session,
                force=False,
            )
        directory = _acquisition_cache_directory(
            context.output_directory, job.physical_id
        )
        expected = _expected_acquisition_lock(
            base_lock=context.base_lock,
            physical_id=job.physical_id,
            item_rows=job.item_rows,
        )
        if not _acquisition_cache_is_current(directory, expected_lock=expected):
            raise ValueError("Completed acquisition failed its cache lock revalidation.")
        return result

    def checkpoint() -> dict[str, Any]:
        return compile_outputs_from_current_caches(
            inventory=context.inventory,
            spatial=context.spatial,
            stage=context.stage,
            research=context.research,
            base_lock=context.base_lock,
            output_directory=context.output_directory,
            runner_sha256=context.runner_sha256,
            runner_version=RUNNER_VERSION,
        )

    all_ids = tuple(context.inventory.acquisitions["physical_acquisition_id"].astype(str))

    def write_progress(snapshot: dict[str, Any]) -> None:
        if snapshot["state"] not in {"running", "pausing"}:
            return
        payload = {
            **context.base_lock,
            "sentinel_stage_config_payload": context.stage.raw,
            "sentinel_research_dependency_payload": _research_dependency_payload(
                context.research
            ),
            "sentinel_feature_pipeline_fingerprint": context.pipeline_payload,
            "state": "building",
            "promoted_outputs_valid": False,
            "expected_physical_acquisition_count": snapshot["total"],
            "completed_physical_acquisition_count": snapshot["completed"],
            "build_complete": False,
            "completed_physical_acquisition_ids_sha256": snapshot[
                "completed_ids_sha256"
            ],
            "dashboard_state_audit_only": snapshot["state"],
            "dashboard_workers_audit_only": workers,
            "dashboard_retry_attempts_total_audit_only": snapshot[
                "retry_attempts_total"
            ],
            "dashboard_retrying_acquisitions_audit_only": snapshot["retrying"],
            "dashboard_last_failure_audit_only": snapshot["last_failure"],
            "dashboard_runner_sha256_audit_only": context.runner_sha256,
            "dashboard_runner_version_audit_only": RUNNER_VERSION,
            "dashboard_updated_at_utc_audit_only": _utc_now(),
        }
        atomic_json(payload, context.output_directory / "build_progress.json")

    return CooperativeAcquisitionRunner(
        jobs=context.jobs,
        all_ids=all_ids,
        initial_completed_ids=context.current_ids,
        worker=process_one,
        checkpoint=checkpoint,
        progress_hook=write_progress,
        workers=workers,
        historical_seconds_per_job=context.historical_seconds_per_acquisition,
    )


class DashboardRequestHandler(BaseHTTPRequestHandler):
    """Serve the local dashboard and its same-origin control API."""

    runner: Any
    control_token: str
    page: bytes

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        if self.path in {"/", "/index.html"}:
            body = self.page
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; style-src 'unsafe-inline'; "
                "script-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/api/status":
            self._send_json(self.runner.snapshot())
            return
        self._send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        if self.headers.get("X-ISEF-Control") != self.control_token:
            self._send_json({"error": "forbidden"}, HTTPStatus.FORBIDDEN)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json({"error": "invalid_content_length"}, HTTPStatus.BAD_REQUEST)
            return
        if length < 0 or length > 4096:
            self._send_json({"error": "request_too_large"}, HTTPStatus.BAD_REQUEST)
            return
        if length:
            self.rfile.read(length)
        if self.path == "/api/start":
            self._send_json(self.runner.start_or_resume())
            return
        if self.path == "/api/pause":
            self._send_json(self.runner.request_pause())
            return
        self._send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)


def create_server(
    *,
    host: str,
    port: int,
    runner: Any,
    page_path: Path,
) -> ThreadingHTTPServer:
    token = secrets.token_urlsafe(24)
    page = page_path.read_text(encoding="utf-8").replace("__CONTROL_TOKEN__", token)
    handler = type(
        "BoundDashboardRequestHandler",
        (DashboardRequestHandler,),
        {"runner": runner, "control_token": token, "page": page.encode("utf-8")},
    )
    return ThreadingHTTPServer((host, port), handler)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research-config", default="configs/research.toml")
    parser.add_argument("--stage-config", default="configs/sentinel_features.toml")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--workers", type=int, choices=(1, 2), default=1)
    parser.add_argument("--no-browser", action="store_true")
    arguments = parser.parse_args()
    if arguments.host not in {"127.0.0.1", "localhost"}:
        raise ValueError("The temporary dashboard may bind only to localhost.")

    project_root = Path(__file__).resolve().parents[2]
    stage = load_sentinel_stage_config(arguments.stage_config)
    output_directory = _resolve_project_path(
        project_root, stage.raw["paths"]["output_directory"]
    )
    with DashboardProcessLock(output_directory / "dashboard.lock"):
        runner = LazyDashboardRunner(
            research_config_path=arguments.research_config,
            stage_config_path=arguments.stage_config,
            workers=arguments.workers,
        )
        page_path = project_root / "tools/sentinel_dashboard/index.html"
        server = create_server(
            host=arguments.host,
            port=arguments.port,
            runner=runner,
            page_path=page_path,
        )
        url = f"http://{arguments.host}:{arguments.port}/"
        print(f"Sentinel dashboard ready at {url}", flush=True)
        runner.begin_initialization()
        if not arguments.no_browser:
            webbrowser.open(url)
        try:
            server.serve_forever(poll_interval=0.5)
        except KeyboardInterrupt:
            print(
                "Safe pause requested; waiting for active acquisition to finish.",
                flush=True,
            )
            runner.request_pause()
            runner.wait_for_final_state(timeout=None)
        finally:
            runner.shutdown()
            server.server_close()


if __name__ == "__main__":
    main()
