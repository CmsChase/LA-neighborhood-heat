# ruff: noqa: E501
"""Low-load localhost UI and process supervisor for M3 source development.

The dashboard is orchestration only.  It does not create an authorization,
open target values, initialize scientific work, or start the worker until the
user presses Start / Continue.  The worker owns all durable checkpoints and
publishes ``status.json``.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import secrets
import subprocess
import sys
import threading
import time
import webbrowser
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Final, Protocol

from la_heat.multicity.m3_source_development_runtime import runtime_readiness

PROJECT_ROOT: Final = Path(__file__).resolve().parents[3]
DEFAULT_HOST: Final = "127.0.0.1"
DEFAULT_PORT: Final = 8772
RUNTIME_RELATIVE_DIRECTORY: Final = Path(
    "data/interim/multicity/m3_source_development/runtime"
)
STATUS_FILENAME: Final = "status.json"
CONTROL_FILENAME: Final = "control.json"
ENGINE_LOG_FILENAME: Final = "worker.log"
DASHBOARD_LOCK_FILENAME: Final = "dashboard.lock"
WORKER_RELATIVE_PATH: Final = Path("scripts/run_m3_source_development_worker.py")

PHASE_CHOICES: Final = ("online_predownload", "offline_qa_rebuild")
DOWNLOAD_WORKER_CHOICES: Final = (1, 2)
DEFAULT_PHASE: Final = "online_predownload"
DEFAULT_DOWNLOAD_WORKERS: Final = 2
DEFAULT_COMPUTE_WORKERS: Final = 1
DEFAULT_WINDOW_SIZE: Final = 512
COMPLETE_STATES: Final = frozenset({"complete", "completed"})
WAITING_NETWORK_STATES: Final = frozenset(
    {"waiting_for_network", "network_offline", "offline_wait"}
)
BLOCKED_READINESS_STATES: Final = frozenset(
    {
        "waiting_for_source_acquisition_amendment",
        "waiting_for_expanded_source_inventory",
        "waiting_for_source_qa_execution_authorization",
        "readiness_check_failed",
    }
)
DOWNLOAD_KINDS: Final = ("download_asset", "finalize_scene", "finalize_download")
QA_REBUILD_KINDS: Final = (
    "qa_overpass",
    "compile_qa_city",
    "finalize_qa_candidates",
)
LOSO_LOCK_STATE: Final = "locked_waiting_for_separate_authorization"
READINESS_MESSAGES: Final = {
    "waiting_for_source_acquisition_amendment": (
        "尚未生成并认证正式的源数据扩展修订；本页面没有权限替你创建它。"
    ),
    "waiting_for_expanded_source_inventory": (
        "源数据扩展修订已存在，但 Houston / Chicago 的固定窗口清单尚未完成。"
    ),
    "waiting_for_source_qa_execution_authorization": (
        "扩展清单已就绪，但源城市下载与 QA 的单独执行授权尚未签发。"
    ),
    "qa_candidates_complete_waiting_for_loso_authorization": (
        "离线 QA 重建已经完成；Nested LOSO 仍锁定，必须另行授权。"
    ),
    "readiness_check_failed": "启动前检查失败；请查看错误信息，任务没有启动。",
}


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _atomic_json(payload: Mapping[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _count(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _text(value: object, *, limit: int = 500) -> str:
    return str(value or "")[:limit]


def _settings(
    phase: object,
    download_workers: object,
    compute_workers: object,
    window_size: object,
) -> tuple[str, int, int, int]:
    if phase not in PHASE_CHOICES:
        raise ValueError(f"phase must be one of {PHASE_CHOICES}")
    if isinstance(download_workers, bool) or download_workers not in DOWNLOAD_WORKER_CHOICES:
        raise ValueError("download_workers must be 1 or 2")
    if isinstance(compute_workers, bool) or compute_workers != DEFAULT_COMPUTE_WORKERS:
        raise ValueError("compute_workers is fixed at 1 for the office profile")
    if isinstance(window_size, bool) or window_size != DEFAULT_WINDOW_SIZE:
        raise ValueError("window_size is fixed at 512 for the office profile")
    return str(phase), int(download_workers), int(compute_workers), int(window_size)


def _progress_group(payload: Mapping[str, Any], *names: str) -> dict[str, int]:
    progress = payload.get("progress")
    containers = [progress] if isinstance(progress, Mapping) else []
    containers.append(payload)
    raw: Mapping[str, Any] = {}
    for container in containers:
        for name in names:
            candidate = container.get(name)
            if isinstance(candidate, Mapping):
                raw = candidate
                break
        if raw:
            break
    completed = _count(raw.get("completed", raw.get("complete", 0)))
    running = _count(raw.get("running", 0))
    total = _count(raw.get("total", 0))
    pending = _count(raw.get("pending", max(0, total - completed - running)))
    failed = _count(raw.get("failed", raw.get("quarantined", 0)))
    return {
        "completed": completed,
        "running": running,
        "pending": pending,
        "failed": failed,
        "total": total,
    }


def _progress_from_kinds(
    payload: Mapping[str, Any], kinds: Sequence[str]
) -> dict[str, int] | None:
    raw = payload.get("counts_by_kind")
    if not isinstance(raw, Mapping) or not any(kind in raw for kind in kinds):
        return None
    result = {"completed": 0, "running": 0, "pending": 0, "failed": 0, "total": 0}
    for kind in kinds:
        value = raw.get(kind)
        if not isinstance(value, Mapping):
            continue
        completed = _count(value.get("complete", value.get("completed", 0)))
        running = _count(value.get("running", 0))
        failed = _count(value.get("quarantined", value.get("failed", 0)))
        total = _count(value.get("total", 0))
        pending = _count(
            value.get("pending", max(0, total - completed - running - failed))
        )
        result["completed"] += completed
        result["running"] += running
        result["pending"] += pending
        result["failed"] += failed
        result["total"] += total
    return result


def _network_status(payload: Mapping[str, Any]) -> dict[str, Any]:
    raw = payload.get("network")
    source = raw if isinstance(raw, Mapping) else {}
    fallback = payload.get("network_state")
    if fallback is None and payload.get("network_allowed") is False:
        fallback = "disabled_offline"
    elif fallback is None and payload.get("network_allowed") is True:
        fallback = "allowed_not_checked"
    state = _text(source.get("state", fallback or "unknown"), limit=40)
    if not state:
        state = "unknown"
    return {
        "state": state,
        "online": state.casefold() == "online",
        "last_checked_at_utc": _text(
            source.get("last_checked_at_utc", payload.get("network_checked_at_utc")),
            limit=80,
        ),
        "message": _text(source.get("message", payload.get("network_message"))),
    }


def _current_tasks(payload: Mapping[str, Any]) -> list[str]:
    raw = payload.get(
        "current_tasks",
        payload.get("active_task_ids", payload.get("current_task", [])),
    )
    values = raw if isinstance(raw, list | tuple) else [raw]
    result: list[str] = []
    for value in values:
        if isinstance(value, Mapping):
            label = value.get("label", value.get("task_id", value.get("unit_id", "")))
        else:
            label = value
        text = _text(label, limit=180)
        if text:
            result.append(text)
    return result[:8]


def _events(payload: Mapping[str, Any]) -> list[dict[str, str]]:
    raw = payload.get("events")
    if not isinstance(raw, list):
        return []
    result: list[dict[str, str]] = []
    for value in raw[-60:]:
        if isinstance(value, Mapping):
            result.append(
                {
                    "at": _text(value.get("at", value.get("at_utc", "")), limit=80),
                    "message": _text(value.get("message", value.get("event", ""))),
                }
            )
        else:
            result.append({"at": "", "message": _text(value)})
    return result


def empty_engine_status() -> dict[str, Any]:
    empty = {"completed": 0, "running": 0, "pending": 0, "failed": 0, "total": 0}
    return {
        "schema_version": 1,
        "state": "not_started",
        "phase": DEFAULT_PHASE,
        "network": {
            "state": "unknown",
            "online": False,
            "last_checked_at_utc": "",
            "message": "",
        },
        "download": dict(empty),
        "qa_rebuild": dict(empty),
        "loso": {**empty, "state": LOSO_LOCK_STATE},
        "loso_locked": True,
        "loso_message": "Nested LOSO 尚未获准；需要在 QA 重建完成后另行授权。",
        "phase_finished": False,
        "worker_task_kind": "",
        "download_bytes_completed": 0,
        "download_bytes_total": 0,
        "current_tasks": [],
        "eta_seconds": None,
        "retry_count": 0,
        "error": None,
        "events": [],
    }


def normalize_engine_status(payload: object) -> dict[str, Any]:
    """Normalize the worker-owned status without inferring scientific completion."""

    if not isinstance(payload, Mapping):
        return empty_engine_status()
    result = empty_engine_status()
    result["schema_version"] = _count(payload.get("schema_version", 1)) or 1
    result["state"] = _text(payload.get("state", "not_started"), limit=80) or "not_started"
    phase = payload.get("active_phase", payload.get("phase", DEFAULT_PHASE))
    result["phase"] = str(phase) if phase in PHASE_CHOICES else DEFAULT_PHASE
    result["network"] = _network_status(payload)
    result["download"] = _progress_from_kinds(payload, DOWNLOAD_KINDS) or _progress_group(
        payload, "download", "predownload"
    )
    result["qa_rebuild"] = _progress_from_kinds(
        payload, QA_REBUILD_KINDS
    ) or _progress_group(payload, "qa_rebuild", "qa")
    result["loso"] = {
        "completed": 0,
        "running": 0,
        "pending": 0,
        "failed": 0,
        "total": 0,
        "state": LOSO_LOCK_STATE,
    }
    result["worker_task_kind"] = _text(payload.get("phase"), limit=80)
    phase_complete = _count(payload.get("phase_complete", 0))
    phase_total = _count(payload.get("phase_total", 0))
    result["phase_finished"] = bool(
        (phase_total > 0 and phase_complete == phase_total)
        or result["worker_task_kind"] == "complete"
    )
    download_raw = payload.get("download")
    download = download_raw if isinstance(download_raw, Mapping) else {}
    result["download_bytes_completed"] = _count(
        download.get("bytes_completed", payload.get("download_bytes_completed", 0))
    )
    result["download_bytes_total"] = _count(
        download.get("bytes_total", payload.get("download_bytes_total", 0))
    )
    result["current_tasks"] = _current_tasks(payload)
    result["eta_seconds"] = _finite_number(
        payload.get("eta_seconds", payload.get("estimated_remaining_seconds"))
    )
    result["retry_count"] = _count(
        payload.get("retry_count", payload.get("automatic_retries", 0))
    )
    error = payload.get("error")
    if isinstance(error, Mapping):
        result["error"] = {
            "type": _text(error.get("type", error.get("error_type", "Error")), limit=120),
            "message": _text(error.get("message", error.get("detail", ""))),
            "retryable": bool(error.get("retryable", False)),
        }
    elif error:
        result["error"] = {"type": "Error", "message": _text(error), "retryable": False}
    elif payload.get("last_error_type"):
        result["error"] = {
            "type": _text(payload.get("last_error_type"), limit=120),
            "message": "单个任务将按断点与退避规则重试。",
            "retryable": True,
        }
    result["events"] = _events(payload)
    return result


def read_engine_status(path: str | Path) -> dict[str, Any]:
    status_path = Path(path)
    if not status_path.is_file():
        return empty_engine_status()
    try:
        return normalize_engine_status(json.loads(status_path.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        result = empty_engine_status()
        result["state"] = "status_unreadable"
        result["error"] = {
            "type": type(error).__name__,
            "message": "status.json is temporarily unreadable",
            "retryable": True,
        }
        return result


def _tail(path: Path, *, maximum_bytes: int = 80_000) -> list[str]:
    if not path.is_file():
        return []
    try:
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - maximum_bytes))
            raw = stream.read()
        return raw.decode("utf-8", errors="replace").splitlines()[-100:]
    except OSError:
        return []


class DashboardControlStore:
    """Persist user intent and the fixed office performance profile."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    @staticmethod
    def defaults() -> dict[str, Any]:
        return {
            "desired_state": "paused",
            "phase": DEFAULT_PHASE,
            "download_workers": DEFAULT_DOWNLOAD_WORKERS,
            "compute_workers": DEFAULT_COMPUTE_WORKERS,
            "window_size": DEFAULT_WINDOW_SIZE,
        }

    def read(self) -> dict[str, Any]:
        with self._lock:
            if self.path.is_file():
                try:
                    value = json.loads(self.path.read_text(encoding="utf-8"))
                    if isinstance(value, Mapping):
                        phase, download, compute, window = _settings(
                            value.get("phase", DEFAULT_PHASE),
                            value.get("download_workers", DEFAULT_DOWNLOAD_WORKERS),
                            value.get("compute_workers", DEFAULT_COMPUTE_WORKERS),
                            value.get("window_size", DEFAULT_WINDOW_SIZE),
                        )
                        desired = value.get("desired_state", "paused")
                        return {
                            "desired_state": desired
                            if desired in {"running", "paused"}
                            else "paused",
                            "phase": phase,
                            "download_workers": download,
                            "compute_workers": compute,
                            "window_size": window,
                        }
                except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
                    pass
            return self.defaults()

    def write(
        self,
        *,
        desired_state: str,
        phase: object,
        download_workers: object,
        compute_workers: object = DEFAULT_COMPUTE_WORKERS,
        window_size: object = DEFAULT_WINDOW_SIZE,
    ) -> None:
        if desired_state not in {"running", "paused"}:
            raise ValueError("desired_state must be running or paused")
        selected_phase, download, compute, window = _settings(
            phase, download_workers, compute_workers, window_size
        )
        with self._lock:
            _atomic_json(
                {
                    "schema_version": 1,
                    "desired_state": desired_state,
                    "phase": selected_phase,
                    "download_workers": download,
                    "compute_workers": compute,
                    "window_size": window,
                    "updated_at_utc": _utc_now(),
                },
                self.path,
            )


class ManagedProcess(Protocol):
    pid: int

    def poll(self) -> int | None: ...


SpawnProcess = Callable[[tuple[str, ...], Path, Mapping[str, str], Path], ManagedProcess]
ReadinessProbe = Callable[[str | Path], Mapping[str, Any]]


def build_worker_command(
    project_root: str | Path,
    *,
    phase: object = DEFAULT_PHASE,
    download_workers: object = DEFAULT_DOWNLOAD_WORKERS,
    compute_workers: object = DEFAULT_COMPUTE_WORKERS,
    window_size: object = DEFAULT_WINDOW_SIZE,
) -> tuple[str, ...]:
    selected_phase, download, compute, window = _settings(
        phase, download_workers, compute_workers, window_size
    )
    root = Path(project_root).resolve()
    return (
        sys.executable,
        str((root / WORKER_RELATIVE_PATH).resolve()),
        "--project-root",
        str(root),
        "--phase",
        selected_phase,
        "--download-workers",
        str(download),
        "--compute-workers",
        str(compute),
        "--window-size",
        str(window),
        "--start",
    )


class _LoggedProcess:
    def __init__(self, process: subprocess.Popen[bytes], stream: Any) -> None:
        self._process = process
        self._stream = stream
        self._closed = False
        self.pid = process.pid

    def poll(self) -> int | None:
        code = self._process.poll()
        if code is not None and not self._closed:
            self._stream.close()
            self._closed = True
        return code


def _spawn_worker(
    command: tuple[str, ...],
    cwd: Path,
    environment: Mapping[str, str],
    log_path: Path,
) -> ManagedProcess:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    stream = log_path.open("ab", buffering=0)
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        process = subprocess.Popen(  # noqa: S603 - fixed local worker, no shell
            command,
            cwd=cwd,
            env=dict(environment),
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=subprocess.STDOUT,
            creationflags=creation_flags,
        )
    except Exception:
        stream.close()
        raise
    return _LoggedProcess(process, stream)


class M3SourceDevelopmentSupervisor:
    """Supervise one cooperative worker and restart unexpected exits."""

    def __init__(
        self,
        project_root: str | Path,
        *,
        spawn: SpawnProcess = _spawn_worker,
        poll_seconds: float = 0.5,
        initial_backoff_seconds: float = 2.0,
        maximum_backoff_seconds: float = 120.0,
        network_backoff_seconds: float = 30.0,
        monotonic: Callable[[], float] = time.monotonic,
        parent_environment: Mapping[str, str] | None = None,
        readiness_probe: ReadinessProbe = runtime_readiness,
    ) -> None:
        self.root = Path(project_root).resolve()
        self.runtime = self.root / RUNTIME_RELATIVE_DIRECTORY
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.status_path = self.runtime / STATUS_FILENAME
        self.log_path = self.runtime / ENGINE_LOG_FILENAME
        self.control = DashboardControlStore(self.runtime / CONTROL_FILENAME)
        self._spawn = spawn
        self._poll = max(0.01, float(poll_seconds))
        self._initial_backoff = max(0.01, float(initial_backoff_seconds))
        self._maximum_backoff = max(self._initial_backoff, float(maximum_backoff_seconds))
        self._network_backoff = max(self._initial_backoff, float(network_backoff_seconds))
        self._monotonic = monotonic
        self._environment = dict(os.environ if parent_environment is None else parent_environment)
        self._readiness_probe = readiness_probe
        self._condition = threading.Condition(threading.RLock())
        self._process: ManagedProcess | None = None
        self._thread: threading.Thread | None = None
        self._closed = False
        self._next_launch: float | None = None
        self._consecutive_failures = 0
        self._automatic_restarts = 0
        self._last_exit_code: int | None = None
        self._start_blocker: dict[str, Any] | None = None
        self._events: deque[dict[str, str]] = deque(maxlen=60)

    def _event(self, message: str) -> None:
        self._events.append({"at": _utc_now(), "message": message})

    def _status(self) -> dict[str, Any]:
        return read_engine_status(self.status_path)

    def _worker_environment(self) -> dict[str, str]:
        environment = dict(self._environment)
        source = str((self.root / "src").resolve())
        existing = environment.get("PYTHONPATH", "")
        temporary = self.runtime / "tmp"
        temporary.mkdir(parents=True, exist_ok=True)
        environment.update(
            {
                "PYTHONPATH": source if not existing else source + os.pathsep + existing,
                "PYTHONUTF8": "1",
                "PYTHONUNBUFFERED": "1",
                "OMP_NUM_THREADS": "1",
                "MKL_NUM_THREADS": "1",
                "OPENBLAS_NUM_THREADS": "1",
                "NUMEXPR_NUM_THREADS": "1",
                "NUMEXPR_MAX_THREADS": "1",
                "GDAL_NUM_THREADS": "1",
                "TMP": str(temporary),
                "TEMP": str(temporary),
                "TMPDIR": str(temporary),
            }
        )
        return environment

    def _readiness_locked(self) -> dict[str, Any]:
        try:
            payload = dict(self._readiness_probe(self.root))
        except Exception as error:  # noqa: BLE001 - report type only, never a secret
            return {
                "state": "readiness_check_failed",
                "error": {
                    "type": type(error).__name__,
                    "message": "启动前检查失败；任务没有启动。",
                    "retryable": False,
                },
            }
        state = _text(payload.get("state"), limit=100)
        if not state:
            return {
                "state": "readiness_check_failed",
                "error": {
                    "type": "ReadinessContractError",
                    "message": "启动前检查没有返回有效状态；任务没有启动。",
                    "retryable": False,
                },
            }
        payload["state"] = state
        return payload

    @staticmethod
    def _readiness_message(payload: Mapping[str, Any]) -> str:
        state = str(payload.get("state", "readiness_check_failed"))
        return READINESS_MESSAGES.get(
            state,
            "启动条件尚未满足；任务没有启动，也不会自动循环重启。",
        )

    def _pause_for_blocker_locked(self, payload: Mapping[str, Any]) -> None:
        settings = self.control.read()
        self.control.write(
            desired_state="paused",
            phase=settings["phase"],
            download_workers=settings["download_workers"],
            compute_workers=settings["compute_workers"],
            window_size=settings["window_size"],
        )
        blocker = dict(payload)
        blocker["message"] = self._readiness_message(payload)
        self._start_blocker = blocker
        self._next_launch = None
        self._consecutive_failures = 0
        self._event(blocker["message"])

    def _launch_locked(self) -> None:
        if self._closed or self._process is not None:
            return
        settings = self.control.read()
        if settings["desired_state"] != "running":
            return
        readiness = self._readiness_locked()
        if readiness.get("state") != "ready_paused":
            self._pause_for_blocker_locked(readiness)
            return
        self._start_blocker = None
        command = build_worker_command(
            self.root,
            phase=settings["phase"],
            download_workers=settings["download_workers"],
            compute_workers=settings["compute_workers"],
            window_size=settings["window_size"],
        )
        try:
            self._process = self._spawn(
                command,
                self.root,
                self._worker_environment(),
                self.log_path,
            )
        except Exception as error:  # noqa: BLE001 - report type, retry supervisor
            self._event(f"Worker 启动失败（{type(error).__name__}），将自动重试。")
            self._schedule_restart_locked(waiting_for_network=False)
            return
        self._next_launch = None
        self._event(
            "Worker 已启动："
            f"{settings['phase']}，下载 {settings['download_workers']}，"
            f"计算 {settings['compute_workers']}，兼容参数 {settings['window_size']}。"
        )

    def _schedule_restart_locked(self, *, waiting_for_network: bool) -> None:
        self._consecutive_failures += 1
        if waiting_for_network:
            delay = self._network_backoff
        else:
            delay = min(
                self._initial_backoff * 2 ** min(self._consecutive_failures - 1, 10),
                self._maximum_backoff,
            )
        self._automatic_restarts += 1
        self._next_launch = self._monotonic() + delay
        reason = "等待网络恢复" if waiting_for_network else "进程异常退出"
        self._event(f"{reason}，{delay:g} 秒后自动重启。")

    def _handle_exit_locked(self, code: int) -> None:
        self._process = None
        self._last_exit_code = code
        status = self._status()
        settings = self.control.read()
        state = str(status["state"]).casefold()
        if settings["desired_state"] == "paused":
            self._next_launch = None
            self._event("已在安全任务边界暂停。")
        elif status.get("phase_finished") or state in COMPLETE_STATES:
            self.control.write(
                desired_state="paused",
                phase=settings["phase"],
                download_workers=settings["download_workers"],
                compute_workers=settings["compute_workers"],
                window_size=settings["window_size"],
            )
            self._next_launch = None
            self._consecutive_failures = 0
            self._event("当前阶段已完成。")
        elif state in BLOCKED_READINESS_STATES:
            self._pause_for_blocker_locked(status)
        elif state == "qa_candidates_complete_waiting_for_loso_authorization":
            self._pause_for_blocker_locked(status)
        else:
            self._schedule_restart_locked(waiting_for_network=state in WAITING_NETWORK_STATES)

    def _supervise(self) -> None:
        while True:
            with self._condition:
                if self._closed:
                    return
                if self._process is not None:
                    code = self._process.poll()
                    if code is not None:
                        self._handle_exit_locked(code)
                settings = self.control.read()
                if (
                    self._process is None
                    and settings["desired_state"] == "running"
                    and (self._next_launch is None or self._monotonic() >= self._next_launch)
                ):
                    self._launch_locked()
                self._condition.wait(timeout=self._poll)

    def begin_supervision(self) -> None:
        with self._condition:
            if self._thread is not None and self._thread.is_alive():
                return
            self._closed = False
            self._thread = threading.Thread(
                target=self._supervise,
                name="m3-source-development-supervisor",
                daemon=True,
            )
            self._thread.start()

    def start_or_continue(
        self,
        *,
        phase: object = DEFAULT_PHASE,
        download_workers: object = DEFAULT_DOWNLOAD_WORKERS,
        compute_workers: object = DEFAULT_COMPUTE_WORKERS,
        window_size: object = DEFAULT_WINDOW_SIZE,
    ) -> dict[str, Any]:
        selected = _settings(phase, download_workers, compute_workers, window_size)
        with self._condition:
            if self._process is not None and self._process.poll() is None:
                current = self.control.read()
                requested = {
                    "phase": selected[0],
                    "download_workers": selected[1],
                    "compute_workers": selected[2],
                    "window_size": selected[3],
                }
                if any(current[key] != value for key, value in requested.items()):
                    raise ValueError("请先安全暂停，再切换阶段或性能设置。")
                return self._snapshot_locked()
            readiness = self._readiness_locked()
            if readiness.get("state") != "ready_paused":
                self._pause_for_blocker_locked(readiness)
                return self._snapshot_locked()
            self._start_blocker = None
            self.control.write(
                desired_state="running",
                phase=selected[0],
                download_workers=selected[1],
                compute_workers=selected[2],
                window_size=selected[3],
            )
            self._next_launch = None
            self._consecutive_failures = 0
            self._event("收到开始 / 继续指令。")
            self._launch_locked()
            self._condition.notify_all()
            return self._snapshot_locked()

    def request_pause(self) -> dict[str, Any]:
        with self._condition:
            settings = self.control.read()
            self.control.write(
                desired_state="paused",
                phase=settings["phase"],
                download_workers=settings["download_workers"],
                compute_workers=settings["compute_workers"],
                window_size=settings["window_size"],
            )
            self._next_launch = None
            self._event("已请求安全暂停；当前任务完成后停止领取新任务。")
            self._condition.notify_all()
            return self._snapshot_locked()

    def _snapshot_locked(self) -> dict[str, Any]:
        status = self._status()
        settings = self.control.read()
        process_running = self._process is not None and self._process.poll() is None
        engine_state = str(status["state"]).casefold()
        desired = settings["desired_state"]
        blocker = self._start_blocker
        if blocker is not None and not process_running:
            public_state = str(blocker["state"])
        elif engine_state in COMPLETE_STATES:
            public_state = "complete"
        elif desired == "paused":
            public_state = "pausing" if process_running else "paused"
        elif engine_state in WAITING_NETWORK_STATES:
            public_state = "waiting_for_network"
        elif process_running:
            public_state = "running"
        elif self._next_launch is not None:
            public_state = "restarting"
        elif engine_state == "not_started":
            public_state = "starting"
        else:
            public_state = engine_state
        retry_in = (
            max(0.0, self._next_launch - self._monotonic())
            if self._next_launch is not None
            else None
        )
        return {
            **status,
            "state": public_state,
            "engine_state": status["state"],
            "desired_state": desired,
            "selected_phase": settings["phase"],
            "download_workers": settings["download_workers"],
            "compute_workers": settings["compute_workers"],
            "window_size": settings["window_size"],
            "worker_process_running": process_running,
            "automatic_restart_count": self._automatic_restarts,
            "consecutive_process_failures": self._consecutive_failures,
            "restart_in_seconds": retry_in,
            "last_exit_code": self._last_exit_code,
            "start_blocked": blocker is not None,
            "action_message": blocker.get("message", "") if blocker else "",
            "readiness_state": blocker.get("state", "ready") if blocker else "ready",
            "events": (status["events"] + list(self._events))[-60:],
            "log_tail": _tail(self.log_path),
            "dashboard_created_authorization": False,
            "worker_started_automatically": False,
        }

    def snapshot(self) -> dict[str, Any]:
        with self._condition:
            return self._snapshot_locked()

    def close(self, *, request_pause: bool = False) -> None:
        if request_pause:
            self.request_pause()
        with self._condition:
            self._closed = True
            self._condition.notify_all()
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5)


_PAGE = r"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>M3 source development</title>
<style>
:root{color-scheme:dark;--bg:#0a0d10;--card:#14191e;--line:#29323a;--text:#f4f7f8;--muted:#91a0aa;--blue:#58a6ff;--cyan:#63d5c8;--gold:#f0c45e;--red:#ff7070}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 80% 0,#162635,transparent 38%),var(--bg);font:15px/1.5 system-ui,"Microsoft YaHei",sans-serif;color:var(--text)}main{max-width:1180px;margin:auto;padding:38px 24px}.top,.statusline{display:flex;justify-content:space-between;align-items:center;gap:16px}h1{font-size:32px;margin:0}.sub{color:var(--muted);margin:6px 0 0}.badge{border:1px solid var(--line);border-radius:99px;padding:7px 13px}.controls{display:grid;grid-template-columns:1.5fr 1fr .75fr .75fr auto auto;gap:12px;margin:26px 0}.control,.card,.stage,.logs{background:rgba(20,25,30,.94);border:1px solid var(--line);border-radius:14px}.control{padding:9px 13px}.control label,.label{display:block;color:var(--muted);font-size:12px}.fixed{font-size:18px;font-weight:800;margin-top:2px}select{width:100%;border:0;outline:0;background:transparent;color:var(--text);font:inherit;font-weight:750}option{background:#14191e}button{border:0;border-radius:12px;padding:0 21px;font:inherit;font-weight:850;cursor:pointer}#start{background:var(--blue);color:#07111b}#pause{background:#293138;color:var(--text)}button:disabled{opacity:.45;cursor:not-allowed}.summary{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:16px}.card{padding:15px}.value{font-size:22px;font-weight:850;margin-top:3px}.stages{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.stage{padding:17px}.stagehead{display:flex;justify-content:space-between;gap:12px}.bar{height:9px;background:#252d34;border-radius:99px;overflow:hidden;margin-top:13px}.fill{height:100%;width:0;background:linear-gradient(90deg,var(--blue),var(--cyan))}.current{margin:20px 2px}.logs{padding:16px;margin-top:15px}.log{height:250px;overflow:auto;background:#080a0c;border-radius:9px;padding:13px;white-space:pre-wrap;font:12px/1.55 Consolas,monospace;color:#bec8ce}.online{color:var(--cyan)}.offline{color:var(--gold)}.error{color:var(--red)}@media(max-width:850px){.controls{grid-template-columns:1fr 1fr}.summary,.stages{grid-template-columns:1fr}.top{align-items:flex-start;flex-direction:column}}
</style></head><body><main><div class="top"><div><h1>M3 source development</h1><p class="sub">办公本低负载 · 联网预下载 → 离线QA重建</p></div><div class="statusline"><span id="network" class="badge">网络：未知</span><span id="state" class="badge">未开始</span></div></div>
<div class="controls"><div class="control"><label for="phase">阶段</label><select id="phase"><option value="online_predownload">1 · 联网预下载</option><option value="offline_qa_rebuild">2 · 离线QA重建</option></select></div><div class="control"><label for="download">下载线程</label><select id="download"><option value="2">2（办公本默认）</option><option value="1">1（最低负载）</option></select></div><div class="control"><label>计算 worker</label><div class="fixed">1</div></div><div class="control"><label>兼容参数</label><div class="fixed">512</div></div><button id="start">开始 / 继续</button><button id="pause">安全暂停</button></div>
<div class="summary"><div class="card"><span class="label">当前阶段</span><div id="active" class="value">—</div></div><div class="card"><span class="label">当前任务</span><div id="currentCount" class="value">0</div></div><div class="card"><span class="label">任务重试</span><div id="retries" class="value">0</div></div><div class="card"><span class="label">进程重启</span><div id="restarts" class="value">0</div></div><div class="card"><span class="label">预计剩余</span><div id="eta" class="value">估算中</div></div></div>
<div class="stages"><div class="stage"><div class="stagehead"><strong>联网预下载</strong><span id="downloadCount">0 / 0</span></div><div class="bar"><div id="downloadBar" class="fill"></div></div><span id="bytes" class="label">0 B / 0 B</span></div><div class="stage"><div class="stagehead"><strong>离线QA重建</strong><span id="qaCount">0 / 0</span></div><div class="bar"><div id="qaBar" class="fill"></div></div><span class="label">None / 3K / 4K / 6K</span></div><div class="stage"><div class="stagehead"><strong>Nested LOSO</strong><span id="losoCount">锁定</span></div><div class="bar"><div id="losoBar" class="fill"></div></div><span class="label">待 QA 完成后另行授权；本页面不会启动</span></div></div>
<div class="current"><strong>正在处理：</strong><span id="current">—</span><span id="message"></span></div><div class="logs"><span class="label">最近日志</span><pre id="log" class="log">等待开始……</pre></div>
</main><script>const control="__TOKEN__",$=id=>document.getElementById(id);let first=true;const stateLabels={not_started:"未开始",paused:"已暂停",pausing:"正在安全暂停",starting:"正在启动",running:"运行中",restarting:"等待自动重启",waiting_for_network:"等待网络",waiting_for_source_acquisition_amendment:"等待正式修订清单",waiting_for_expanded_source_inventory:"等待扩展清单",waiting_for_source_qa_execution_authorization:"等待源城市 QA 执行授权",readiness_check_failed:"启动前检查失败",qa_candidates_complete_waiting_for_loso_authorization:"QA 已完成；LOSO 待另行授权",complete:"已完成"},phaseLabels={online_predownload:"联网预下载",offline_qa_rebuild:"离线QA重建"},networkLabels={unknown:"未知",online:"在线",offline:"离线",allowed_not_checked:"允许联网（尚未检测）",disabled_offline:"离线阶段禁止联网"};function duration(v){if(v==null)return"估算中";v=Math.max(0,Math.round(v));let h=Math.floor(v/3600),m=Math.floor(v%3600/60),s=v%60;return h?`${h}h ${m}m`:m?`${m}m ${s}s`:`${s}s`}function size(v){v=Number(v||0);for(const u of ["B","KiB","MiB","GiB","TiB"]){if(v<1024||u==="TiB")return`${v.toFixed(u==="B"?0:1)} ${u}`;v/=1024}}function stage(s,name,count,bar){const x=s[name]||{},d=x.completed||0,t=x.total||0,p=t?100*d/t:0;$(count).textContent=`${d} / ${t}`;$(bar).style.width=`${Math.min(100,p)}%`}function render(s){$("state").textContent=stateLabels[s.state]||s.state||"—";const n=s.network||{},ns=n.state||"unknown";$("network").textContent=`网络：${networkLabels[ns]||ns}`;$("network").className=`badge ${n.online?"online":ns==="offline"?"offline":""}`;const selected=s.selected_phase||s.phase||"—";$("active").textContent=phaseLabels[selected]||selected;$("currentCount").textContent=(s.current_tasks||[]).length;$("retries").textContent=s.retry_count||0;$("restarts").textContent=s.automatic_restart_count||0;$("eta").textContent=duration(s.eta_seconds);stage(s,"download","downloadCount","downloadBar");stage(s,"qa_rebuild","qaCount","qaBar");$("losoCount").textContent="锁定";$("losoBar").style.width="0";$("bytes").textContent=`${size(s.download_bytes_completed)} / ${size(s.download_bytes_total)}`;$("current").textContent=(s.current_tasks||[]).join(" · ")||"—";const message=s.action_message||s.error&&`${s.error.type}: ${s.error.message}`||s.restart_in_seconds!=null&&`${Math.ceil(s.restart_in_seconds)} 秒后自动重启`||"";$("message").textContent=message?` · ${message}`:"";$("message").className=s.start_blocked||s.error?"error":"";if(first){$("phase").value=s.selected_phase||"online_predownload";$("download").value=String(s.download_workers||2);first=false}const locked=!!s.worker_process_running;$("phase").disabled=locked;$("download").disabled=locked;$("pause").disabled=s.desired_state==="paused"&&!locked;const lines=[...(s.events||[]).map(e=>`${e.at||""}  ${e.message||""}`),...(s.log_tail||[])];$("log").textContent=lines.slice(-100).join("\n")||"等待开始……";$("log").scrollTop=$("log").scrollHeight}async function action(path,body={}){const r=await fetch(path,{method:"POST",headers:{"Content-Type":"application/json","X-M3-Control":control},body:JSON.stringify(body)}),j=await r.json();if(!r.ok)throw Error(j.error||r.statusText);render(j)}async function refresh(){const r=await fetch("/api/status",{cache:"no-store"});render(await r.json())}$("start").onclick=()=>action("/api/start",{phase:$("phase").value,download_workers:Number($("download").value),compute_workers:1,window_size:512}).catch(e=>alert(e.message));$("pause").onclick=()=>action("/api/pause").catch(e=>alert(e.message));refresh().catch(()=>{$("state").textContent="控制页连接失败"});setInterval(()=>refresh().catch(()=>{}),1000);</script></body></html>"""


class _DashboardHTTPServer(ThreadingHTTPServer):
    supervisor: M3SourceDevelopmentSupervisor
    control_token: str


class _Handler(BaseHTTPRequestHandler):
    server: _DashboardHTTPServer

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _send_json(
        self, payload: Mapping[str, Any], status: HTTPStatus = HTTPStatus.OK
    ) -> None:
        encoded = json.dumps(dict(payload), ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/":
            encoded = _PAGE.replace("__TOKEN__", self.server.control_token).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(encoded)
        elif self.path == "/api/status":
            self._send_json(self.server.supervisor.snapshot())
        elif self.path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
        else:
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 16_384:
                raise ValueError("request body is too large")
            raw_body = self.rfile.read(length)
            if self.headers.get("X-M3-Control") != self.server.control_token:
                self._send_json({"error": "forbidden"}, HTTPStatus.FORBIDDEN)
                return
            body = json.loads(raw_body or b"{}")
            if not isinstance(body, dict):
                raise ValueError("request body must be an object")
            if self.path == "/api/start":
                payload = self.server.supervisor.start_or_continue(
                    phase=body.get("phase", DEFAULT_PHASE),
                    download_workers=body.get(
                        "download_workers", DEFAULT_DOWNLOAD_WORKERS
                    ),
                    compute_workers=body.get(
                        "compute_workers", DEFAULT_COMPUTE_WORKERS
                    ),
                    window_size=body.get("window_size", DEFAULT_WINDOW_SIZE),
                )
            elif self.path == "/api/pause":
                payload = self.server.supervisor.request_pause()
            else:
                self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                return
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            self._send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            return
        self._send_json(payload)


def create_server(
    *, host: str, port: int, supervisor: M3SourceDevelopmentSupervisor
) -> _DashboardHTTPServer:
    server = _DashboardHTTPServer((host, port), _Handler)
    server.supervisor = supervisor
    server.control_token = secrets.token_urlsafe(24)
    return server


class DashboardAlreadyRunningError(RuntimeError):
    pass


class DashboardProcessLock:
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
            else:  # pragma: no cover
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as error:
            stream.close()
            raise DashboardAlreadyRunningError("M3 控制页面已经在运行。") from error
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
            else:  # pragma: no cover
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()
            self._stream = None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true")
    return parser


def _configure_project_temp(project_root: str | Path) -> Path:
    temporary = (
        Path(project_root).resolve()
        / RUNTIME_RELATIVE_DIRECTORY
        / ".tmp"
    )
    temporary.mkdir(parents=True, exist_ok=True)
    for name in ("TEMP", "TMP", "TMPDIR"):
        os.environ[name] = str(temporary)
    return temporary


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    _configure_project_temp(arguments.project_root)
    supervisor = M3SourceDevelopmentSupervisor(arguments.project_root)
    lock_path = supervisor.runtime / DASHBOARD_LOCK_FILENAME
    try:
        with DashboardProcessLock(lock_path):
            server = create_server(
                host=str(arguments.host), port=int(arguments.port), supervisor=supervisor
            )
            supervisor.begin_supervision()
            url = f"http://{arguments.host}:{server.server_address[1]}/"
            print(f"M3 source development dashboard: {url}", flush=True)
            print("The worker remains paused until Start / Continue is clicked.", flush=True)
            if not arguments.no_browser:
                webbrowser.open(url)
            try:
                server.serve_forever(poll_interval=0.25)
            except KeyboardInterrupt:
                print("Safe pause requested.", flush=True)
            finally:
                supervisor.request_pause()
                server.server_close()
    except DashboardAlreadyRunningError as error:
        print(str(error), file=sys.stderr)
        return 2
    finally:
        supervisor.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
