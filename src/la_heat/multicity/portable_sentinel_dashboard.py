"""Local progress UI and process supervisor for the four-city Sentinel build.

This module only orchestrates ``scripts/build_portable_sentinel_features.py``.
The scientific engine owns checkpoints and writes ``status.json``.  A pause is
cooperative: the dashboard creates ``PAUSE_REQUESTED`` and the engine exits at
the next acquisition boundary, so a partially processed acquisition is never
presented as complete.
"""

# ruff: noqa: E501

from __future__ import annotations

import argparse
import hmac
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

DEFAULT_HOST: Final = "127.0.0.1"
DEFAULT_PORT: Final = 8769
ENGINE_RELATIVE_PATH: Final = Path("scripts/build_portable_sentinel_features.py")
RUNTIME_RELATIVE_DIRECTORY: Final = Path(
    "data/interim/multicity/portable_predictors/runtime/sentinel"
)
STATUS_FILENAME: Final = "status.json"
CONTROL_FILENAME: Final = "dashboard_control.json"
PAUSE_FILENAME: Final = "PAUSE_REQUESTED"
ENGINE_LOG_FILENAME: Final = "engine.log"
DOWNLOAD_THREAD_CHOICES: Final = (6, 8)
ACQUISITION_CONCURRENCY_CHOICES: Final = (1, 2)
DEFAULT_DOWNLOAD_THREADS: Final = 6
DEFAULT_ACQUISITION_CONCURRENCY: Final = 1
COMPLETE_STATES: Final = frozenset({"complete", "completed"})
PAUSED_STATES: Final = frozenset({"paused", "pause", "pause_requested"})


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _atomic_json(payload: Mapping[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _count(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int | float) and math.isfinite(float(value)):
        return max(0, int(value))
    if isinstance(value, list | tuple | set | dict):
        return len(value)
    return 0


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) and number >= 0 else None


def _text(value: object, *, limit: int = 500) -> str:
    return str(value).replace("\r", " ").replace("\n", " ").strip()[:limit]


def _current(value: object) -> list[str]:
    values = value if isinstance(value, list | tuple) else [value]
    result: list[str] = []
    for item in values:
        if item is None:
            continue
        if isinstance(item, dict):
            selected = next(
                (
                    item[key]
                    for key in (
                        "task_id",
                        "acquisition_key",
                        "acquisition_id",
                        "item_id",
                        "id",
                        "name",
                    )
                    if key in item
                ),
                None,
            )
            if selected is not None:
                result.append(_text(selected, limit=180))
        else:
            result.append(_text(item, limit=180))
    return result[:12]


def _events(value: object) -> list[dict[str, str]]:
    values = value if isinstance(value, list | tuple) else [value]
    result: list[dict[str, str]] = []
    for item in values:
        if item is None:
            continue
        if isinstance(item, dict):
            at = item.get("at", item.get("time", ""))
            message = item.get("message", item.get("event", item.get("type", "")))
        else:
            at, message = "", item
        if message:
            result.append({"at": _text(at, limit=80), "message": _text(message)})
    return result[-60:]


def _city_progress(value: object) -> list[dict[str, Any]]:
    rows: list[tuple[str, object]] = []
    if isinstance(value, dict):
        rows = [(str(city), detail) for city, detail in value.items()]
    elif isinstance(value, list):
        rows = [
            (str(item.get("city_id", item.get("city", ""))), item)
            for item in value
            if isinstance(item, dict)
        ]
    result: list[dict[str, Any]] = []
    for city, detail in rows:
        if not city:
            continue
        mapping = detail if isinstance(detail, dict) else {}
        total = _count(mapping.get("total", mapping.get("total_tasks", 0)))
        completed = min(
            total or sys.maxsize,
            _count(mapping.get("completed", mapping.get("completed_tasks", 0))),
        )
        if total == 0:
            completed = _count(mapping.get("completed", mapping.get("completed_tasks", 0)))
        result.append(
            {
                "city_id": _text(city, limit=80),
                "total": total,
                "completed": completed,
                "failed": _count(mapping.get("failed", mapping.get("failures", 0))),
                "running": _count(mapping.get("running", mapping.get("active", 0))),
            }
        )
    order = {
        "los_angeles_ca": 0,
        "phoenix_az": 1,
        "houston_tx": 2,
        "chicago_il": 3,
    }
    result.sort(key=lambda row: (order.get(str(row["city_id"]), 99), row["city_id"]))
    return result


def empty_engine_status() -> dict[str, Any]:
    return {
        "state": "not_started",
        "phase": "准备",
        "current_city": None,
        "total": 0,
        "completed": 0,
        "pending": 0,
        "running": 0,
        "failed": 0,
        "retries": 0,
        "current": [],
        "eta_seconds": None,
        "cities": [],
        "events": [],
        "error": None,
        "updated_at_utc": None,
        "status_contract_error": None,
    }


def normalize_engine_status(payload: object) -> dict[str, Any]:
    """Normalize the small engine/status contract used by the browser."""

    result = empty_engine_status()
    if not isinstance(payload, dict):
        result["status_contract_error"] = "status JSON is not an object"
        return result
    state = payload.get("state")
    if isinstance(state, str) and state.strip():
        result["state"] = _text(state, limit=100)
    phase = payload.get("phase", payload.get("stage"))
    if isinstance(phase, str) and phase.strip():
        result["phase"] = _text(phase, limit=140)
    current_value = payload.get(
        "current", payload.get("active_tasks", payload.get("current_task", []))
    )
    city = payload.get("current_city", payload.get("city_id", payload.get("city")))
    if city is None and isinstance(current_value, dict):
        city = current_value.get("city_id", current_value.get("city"))
    if city is not None:
        result["current_city"] = _text(city, limit=80)

    total = _count(payload.get("total", payload.get("total_tasks", 0)))
    completed = _count(payload.get("completed", payload.get("completed_tasks", 0)))
    completed = min(total, completed) if total else completed
    running = _count(payload.get("running", payload.get("active_tasks", 0)))
    failed = _count(payload.get("failed", payload.get("failures", 0)))
    pending_raw = payload.get("pending")
    pending = (
        _count(pending_raw) if pending_raw is not None else max(0, total - completed - running)
    )
    result.update(
        {
            "total": total,
            "completed": completed,
            "pending": pending,
            "running": running,
            "failed": failed,
            "retries": _count(
                payload.get(
                    "retries",
                    payload.get("retry_count", payload.get("automatic_retries", 0)),
                )
            ),
            "current": _current(current_value),
            "eta_seconds": _number(
                payload.get("eta_seconds", payload.get("estimated_remaining_seconds"))
            ),
            "cities": _city_progress(payload.get("cities", payload.get("city_progress", []))),
            "events": _events(
                payload.get("events", payload.get("log_tail", payload.get("logs", [])))
            ),
            "updated_at_utc": (
                _text(payload.get("updated_at_utc", payload.get("updated_at")), limit=100)
                if payload.get("updated_at_utc", payload.get("updated_at")) is not None
                else None
            ),
        }
    )
    error = payload.get("error")
    if error is not None:
        if isinstance(error, dict):
            result["error"] = {
                "type": _text(error.get("type", error.get("error_type", "Error")), limit=100),
                "message": _text(error.get("message", error), limit=500),
                "retryable": bool(error.get("retryable", False)),
            }
        else:
            result["error"] = {"type": "Error", "message": _text(error)}
    return result


def read_engine_status(path: str | Path) -> dict[str, Any]:
    status_path = Path(path)
    if not status_path.is_file():
        return empty_engine_status()
    try:
        return normalize_engine_status(json.loads(status_path.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, json.JSONDecodeError):
        result = empty_engine_status()
        result["status_contract_error"] = "status JSON could not be read"
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
        return raw.decode("utf-8", errors="replace").splitlines()[-80:]
    except OSError:
        return []


def _settings(download_threads: object, acquisition_concurrency: object) -> tuple[int, int]:
    if isinstance(download_threads, bool) or download_threads not in DOWNLOAD_THREAD_CHOICES:
        raise ValueError("download_threads must be 6 or 8")
    if (
        isinstance(acquisition_concurrency, bool)
        or acquisition_concurrency not in ACQUISITION_CONCURRENCY_CHOICES
    ):
        raise ValueError("acquisition_concurrency must be 1 or 2")
    return int(download_threads), int(acquisition_concurrency)


class DashboardControlStore:
    """Persist only the desired state and two user-selected performance settings."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def read(self) -> dict[str, Any]:
        with self._lock:
            if self.path.is_file():
                try:
                    value = json.loads(self.path.read_text(encoding="utf-8"))
                    if isinstance(value, dict):
                        desired = value.get("desired_state", "paused")
                        download, concurrent = _settings(
                            value.get("download_threads", DEFAULT_DOWNLOAD_THREADS),
                            value.get(
                                "acquisition_concurrency",
                                DEFAULT_ACQUISITION_CONCURRENCY,
                            ),
                        )
                        return {
                            "desired_state": desired
                            if desired in {"running", "paused"}
                            else "paused",
                            "download_threads": download,
                            "acquisition_concurrency": concurrent,
                        }
                except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
                    pass
            return {
                "desired_state": "paused",
                "download_threads": DEFAULT_DOWNLOAD_THREADS,
                "acquisition_concurrency": DEFAULT_ACQUISITION_CONCURRENCY,
            }

    def write(
        self,
        *,
        desired_state: str,
        download_threads: int,
        acquisition_concurrency: int,
    ) -> None:
        if desired_state not in {"running", "paused"}:
            raise ValueError("desired_state must be running or paused")
        download, concurrent = _settings(download_threads, acquisition_concurrency)
        with self._lock:
            _atomic_json(
                {
                    "schema_version": 1,
                    "desired_state": desired_state,
                    "download_threads": download,
                    "acquisition_concurrency": concurrent,
                    "updated_at_utc": _utc_now(),
                },
                self.path,
            )


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
            raise DashboardAlreadyRunningError("Sentinel 控制页面已经在运行。") from error
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


class ManagedProcess(Protocol):
    pid: int

    def poll(self) -> int | None: ...


SpawnProcess = Callable[[tuple[str, ...], Path, Mapping[str, str], Path], ManagedProcess]


def build_engine_command(
    project_root: str | Path,
    *,
    download_threads: int = DEFAULT_DOWNLOAD_THREADS,
    acquisition_concurrency: int = DEFAULT_ACQUISITION_CONCURRENCY,
) -> tuple[str, ...]:
    download, concurrent = _settings(download_threads, acquisition_concurrency)
    engine = (Path(project_root).resolve() / ENGINE_RELATIVE_PATH).resolve()
    return (
        sys.executable,
        str(engine),
        "--download-threads",
        str(download),
        "--acquisition-concurrency",
        str(concurrent),
    )


class _PopenWithLog:
    def __init__(self, process: subprocess.Popen[bytes], stream: Any) -> None:
        self._process = process
        self._stream = stream
        self.pid = process.pid
        self._closed = False

    def poll(self) -> int | None:
        code = self._process.poll()
        if code is not None and not self._closed:
            self._stream.close()
            self._closed = True
        return code


def _spawn_engine(
    command: tuple[str, ...],
    cwd: Path,
    environment: Mapping[str, str],
    log_path: Path,
) -> ManagedProcess:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    stream = log_path.open("ab", buffering=0)
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        process = subprocess.Popen(  # noqa: S603 - local fixed script, no shell
            command,
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=subprocess.STDOUT,
            creationflags=creation_flags,
        )
    except Exception:
        stream.close()
        raise
    return _PopenWithLog(process, stream)


def _write_pause_marker(path: Path) -> None:
    _atomic_json({"intent": "pause", "requested_at_utc": _utc_now()}, path)


class PortableSentinelSupervisor:
    """Run one resumable engine process and restart unexpected exits."""

    def __init__(
        self,
        project_root: str | Path,
        *,
        spawn: SpawnProcess = _spawn_engine,
        poll_seconds: float = 0.5,
        initial_backoff_seconds: float = 2.0,
        maximum_backoff_seconds: float = 300.0,
        monotonic: Callable[[], float] = time.monotonic,
        parent_environment: Mapping[str, str] | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.runtime_directory = self.project_root / RUNTIME_RELATIVE_DIRECTORY
        self.runtime_directory.mkdir(parents=True, exist_ok=True)
        self.status_path = self.runtime_directory / STATUS_FILENAME
        self.pause_path = self.runtime_directory / PAUSE_FILENAME
        self.log_path = self.runtime_directory / ENGINE_LOG_FILENAME
        self.control = DashboardControlStore(self.runtime_directory / CONTROL_FILENAME)
        self._spawn = spawn
        self._poll_seconds = max(0.01, float(poll_seconds))
        self._initial_backoff = max(0.01, float(initial_backoff_seconds))
        self._maximum_backoff = max(self._initial_backoff, float(maximum_backoff_seconds))
        self._monotonic = monotonic
        self._environment = dict(os.environ if parent_environment is None else parent_environment)
        self._condition = threading.Condition(threading.RLock())
        self._child: ManagedProcess | None = None
        self._closed = False
        self._monitor: threading.Thread | None = None
        self._next_launch: float | None = None
        self._consecutive_failures = 0
        self._automatic_restarts = 0
        self._last_exit_code: int | None = None
        self._events: deque[dict[str, str]] = deque(maxlen=60)

    def _event(self, message: str) -> None:
        self._events.append({"at": _utc_now(), "message": message})

    def _status(self) -> dict[str, Any]:
        return read_engine_status(self.status_path)

    def _launch_locked(self) -> None:
        if self._closed or self._child is not None or self.pause_path.exists():
            return
        settings = self.control.read()
        if settings["desired_state"] != "running":
            return
        command = build_engine_command(
            self.project_root,
            download_threads=settings["download_threads"],
            acquisition_concurrency=settings["acquisition_concurrency"],
        )
        environment = dict(self._environment)
        temporary = self.runtime_directory / "tmp"
        temporary.mkdir(parents=True, exist_ok=True)
        source_directory = str((self.project_root / "src").resolve())
        existing_python_path = environment.get("PYTHONPATH", "")
        environment.update(
            {
                "TMP": str(temporary),
                "TEMP": str(temporary),
                "TMPDIR": str(temporary),
                "PYTHONPATH": (
                    source_directory
                    if not existing_python_path
                    else source_directory + os.pathsep + existing_python_path
                ),
            }
        )
        try:
            self._child = self._spawn(command, self.project_root, environment, self.log_path)
        except Exception as error:  # noqa: BLE001
            self._event(f"启动失败（{type(error).__name__}），将自动重试。")
            self._schedule_restart_locked()
            return
        self._next_launch = None
        self._event(
            "Sentinel engine 已启动："
            f"下载 {settings['download_threads']} 线程，"
            f"同时处理 {settings['acquisition_concurrency']} 个 acquisition。"
        )

    def _schedule_restart_locked(self) -> None:
        self._consecutive_failures += 1
        delay = min(
            self._initial_backoff * 2 ** min(self._consecutive_failures - 1, 20),
            self._maximum_backoff,
        )
        self._automatic_restarts += 1
        self._next_launch = self._monotonic() + delay
        self._event(f"进程异常退出，{delay:g} 秒后自动重启。")

    def _handle_exit_locked(self, code: int) -> None:
        self._child = None
        self._last_exit_code = code
        status = self._status()
        desired = self.control.read()["desired_state"]
        if desired == "paused" or self.pause_path.exists():
            self._next_launch = None
            self._event("已在 acquisition 边界安全暂停。")
        elif str(status["state"]).lower() in COMPLETE_STATES:
            settings = self.control.read()
            self.control.write(
                desired_state="paused",
                download_threads=settings["download_threads"],
                acquisition_concurrency=settings["acquisition_concurrency"],
            )
            self._next_launch = None
            self._consecutive_failures = 0
            self._event("四城 Sentinel 特征构建完成。")
        else:
            self._schedule_restart_locked()

    def _loop(self) -> None:
        while True:
            with self._condition:
                if self._closed:
                    return
                if self._child is not None:
                    code = self._child.poll()
                    if code is not None:
                        self._handle_exit_locked(code)
                settings = self.control.read()
                if (
                    self._child is None
                    and settings["desired_state"] == "running"
                    and not self.pause_path.exists()
                    and (self._next_launch is None or self._monotonic() >= self._next_launch)
                ):
                    self._launch_locked()
                self._condition.wait(timeout=self._poll_seconds)

    def begin_supervision(self) -> None:
        with self._condition:
            if self._monitor is not None:
                return
            self._monitor = threading.Thread(
                target=self._loop, name="portable-sentinel-supervisor", daemon=True
            )
            self._monitor.start()

    def start_or_continue(
        self,
        *,
        download_threads: object = DEFAULT_DOWNLOAD_THREADS,
        acquisition_concurrency: object = DEFAULT_ACQUISITION_CONCURRENCY,
    ) -> dict[str, Any]:
        download, concurrent = _settings(download_threads, acquisition_concurrency)
        with self._condition:
            self.pause_path.unlink(missing_ok=True)
            self.control.write(
                desired_state="running",
                download_threads=download,
                acquisition_concurrency=concurrent,
            )
            self._next_launch = None
            self._consecutive_failures = 0
            self._event("收到开始/继续指令。")
            if self._child is not None:
                exit_code = self._child.poll()
                if exit_code is not None:
                    self._child = None
                    self._last_exit_code = exit_code
            if self._child is None:
                self._launch_locked()
            self._condition.notify_all()
            return self._snapshot_locked()

    def request_pause(self) -> dict[str, Any]:
        with self._condition:
            settings = self.control.read()
            self.control.write(
                desired_state="paused",
                download_threads=settings["download_threads"],
                acquisition_concurrency=settings["acquisition_concurrency"],
            )
            _write_pause_marker(self.pause_path)
            self._next_launch = None
            self._event("已请求暂停；当前 acquisition 完成后停止。")
            self._condition.notify_all()
            return self._snapshot_locked()

    def _snapshot_locked(self) -> dict[str, Any]:
        status = self._status()
        settings = self.control.read()
        child_running = self._child is not None and self._child.poll() is None
        desired = settings["desired_state"]
        state = str(status["state"]).lower()
        if desired == "paused":
            public_state = "pausing" if child_running else "paused"
        elif child_running:
            public_state = "running"
        elif self._next_launch is not None:
            public_state = "restarting"
        elif state in COMPLETE_STATES:
            public_state = "complete"
        else:
            public_state = state
        retry_in = (
            max(0.0, self._next_launch - self._monotonic())
            if self._next_launch is not None
            else None
        )
        combined_events = (status["events"] + list(self._events))[-60:]
        return {
            **status,
            "state": public_state,
            "engine_state": status["state"],
            "desired_state": desired,
            "download_threads": settings["download_threads"],
            "acquisition_concurrency": settings["acquisition_concurrency"],
            "engine_process_running": child_running,
            "pause_requested": self.pause_path.exists(),
            "automatic_restart_count": self._automatic_restarts,
            "consecutive_process_failures": self._consecutive_failures,
            "restart_in_seconds": retry_in,
            "last_exit_code": self._last_exit_code,
            "events": combined_events,
            "log_tail": _tail(self.log_path),
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
            monitor = self._monitor
        if monitor is not None:
            monitor.join(timeout=max(1.0, self._poll_seconds * 4))


_PAGE = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>四城 Sentinel-2 特征构建</title>
<style>
:root{color-scheme:dark;--bg:#0c0f12;--card:#151a1f;--line:#293139;--text:#f3f5f7;--muted:#97a4ad;--blue:#4e9cf5;--cyan:#62d8d0;--amber:#ffbd59;--red:#ff6b6b}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 80% 0,#15232b 0,transparent 38%),var(--bg);color:var(--text);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{width:min(1220px,calc(100% - 32px));margin:32px auto 60px}.top{display:flex;justify-content:space-between;gap:20px;align-items:end}.eyebrow{color:var(--cyan);font-size:12px;font-weight:800;letter-spacing:.16em;text-transform:uppercase}h1{font-size:clamp(28px,4vw,46px);margin:4px 0}p{color:var(--muted);margin:4px 0}.state{padding:9px 14px;border:1px solid var(--line);border-radius:99px;font-weight:750}
.controls,.summary,.cities{display:grid;gap:12px}.controls{grid-template-columns:1.2fr 1.2fr auto auto;margin:26px 0 18px}.control,.card,.city,.logs{background:rgba(21,26,31,.9);border:1px solid var(--line);border-radius:14px}.control{padding:10px 14px}.control label{display:block;color:var(--muted);font-size:12px;margin-bottom:3px}select{width:100%;background:transparent;color:var(--text);border:0;font:inherit;font-weight:700;outline:0}option{background:#151a1f}button{border:0;border-radius:12px;padding:0 24px;font:inherit;font-weight:800;cursor:pointer}#start{background:var(--blue);color:#07111b}#pause{background:#2b3238;color:var(--text)}button:disabled{opacity:.45;cursor:not-allowed}
.bar{height:12px;background:#252d34;border-radius:99px;overflow:hidden}.fill{height:100%;width:0;background:linear-gradient(90deg,var(--blue),var(--cyan));transition:width .4s}.progressline{display:flex;justify-content:space-between;margin:10px 2px;color:var(--muted)}.progressline strong{color:var(--text)}
.summary{grid-template-columns:repeat(6,1fr);margin:18px 0}.card{padding:16px}.label{color:var(--muted);font-size:12px}.value{font-size:24px;font-weight:800;margin-top:3px}.cities{grid-template-columns:repeat(4,1fr);margin:18px 0}.city{padding:16px}.cityhead{display:flex;justify-content:space-between;font-weight:800}.mini{height:5px;background:#273039;border-radius:9px;margin-top:12px;overflow:hidden}.minifill{height:100%;background:var(--cyan)}
.current{margin:18px 2px}.current b{color:var(--amber)}.logs{padding:16px;margin-top:18px}.logs h2{font-size:16px;margin:0 0 12px}.log{height:260px;overflow:auto;background:#090c0f;border-radius:9px;padding:12px;color:#b8c4cb;font:12px/1.55 ui-monospace,SFMono-Regular,Consolas,monospace;white-space:pre-wrap}.warning{color:var(--amber)}.error{color:var(--red)}
@media(max-width:850px){.controls{grid-template-columns:1fr 1fr}.summary{grid-template-columns:repeat(3,1fr)}.cities{grid-template-columns:repeat(2,1fr)}}@media(max-width:520px){.controls,.summary,.cities{grid-template-columns:1fr}.top{align-items:start;flex-direction:column}button{height:48px}}
</style></head><body><main>
<div class="top"><div><div class="eyebrow">Portable build runner</div><h1>四城 Sentinel-2 特征构建</h1><p>Los Angeles · Phoenix · Houston · Chicago</p></div><div id="state" class="state">准备</div></div>
<div class="controls"><div class="control"><label for="download">下载线程</label><select id="download"><option value="6">6（推荐）</option><option value="8">8</option></select></div><div class="control"><label for="concurrency">同时处理 acquisition</label><select id="concurrency"><option value="1">1（推荐，最稳）</option><option value="2">2（32GB 可尝试）</option></select></div><button id="start">开始 / 继续</button><button id="pause">安全暂停</button></div>
<div class="progressline"><strong id="count">0 / 0</strong><span id="eta">预计剩余：估算中</span></div><div class="bar"><div id="fill" class="fill"></div></div>
<div class="summary"><div class="card"><div class="label">阶段</div><div id="phase" class="value">准备</div></div><div class="card"><div class="label">完成</div><div id="done" class="value">0</div></div><div class="card"><div class="label">运行</div><div id="running" class="value">0</div></div><div class="card"><div class="label">待处理</div><div id="pending" class="value">0</div></div><div class="card"><div class="label">任务重试</div><div id="retries" class="value">0</div></div><div class="card"><div class="label">进程重启</div><div id="restarts" class="value">0</div></div></div>
<div id="cities" class="cities"></div><div class="current">当前：<b id="current">—</b><span id="message"></span></div>
<div class="logs"><h2>最近日志</h2><div id="log" class="log">等待开始…</div></div>
</main><script>
const control="__CONTROL_TOKEN__";const $=id=>document.getElementById(id);let first=true;
const names={los_angeles_ca:"Los Angeles",phoenix_az:"Phoenix",houston_tx:"Houston",chicago_il:"Chicago"};
function duration(v){if(v==null)return "估算中";v=Math.max(0,Math.round(v));const h=Math.floor(v/3600),m=Math.floor(v%3600/60),s=v%60;return h?`${h} 小时 ${m} 分钟`:m?`${m} 分钟 ${s} 秒`:`${s} 秒`}
function esc(v){return String(v??"").replace(/[&<>\"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]))}
async function action(path,body){const r=await fetch(path,{method:"POST",headers:{"Content-Type":"application/json","X-Sentinel-Control":control},body:JSON.stringify(body)});const j=await r.json();if(!r.ok)throw Error(j.error||r.statusText);render(j)}
function render(s){const total=s.total||0,done=s.completed||0,pct=total?100*done/total:0;$("fill").style.width=`${Math.min(100,pct)}%`;$("count").textContent=`${done} / ${total}  (${pct.toFixed(1)}%)`;$("eta").textContent=`预计剩余：${duration(s.eta_seconds)}`;$("state").textContent=s.state||"—";$("phase").textContent=s.phase||"—";$("done").textContent=done;$("running").textContent=s.running||0;$("pending").textContent=s.pending||0;$("retries").textContent=s.retries||0;$("restarts").textContent=s.automatic_restart_count||0;
if(first){$("download").value=String(s.download_threads||6);$("concurrency").value=String(s.acquisition_concurrency||1);first=false}const locked=!!s.engine_process_running;$("download").disabled=locked;$("concurrency").disabled=locked;$("pause").disabled=s.desired_state==="paused"&&!locked;
const current=[s.current_city,...(s.current||[])].filter(Boolean);$("current").textContent=current.length?current.join(" · "):"—";$("message").textContent=s.error?` · ${s.error.message||s.error.type}`:s.restart_in_seconds!=null?` · ${Math.ceil(s.restart_in_seconds)} 秒后重启`:"";$("message").className=s.error?"error":"warning";
const cities=s.cities||[];$("cities").innerHTML=(cities.length?cities:["los_angeles_ca","phoenix_az","houston_tx","chicago_il"].map(city_id=>({city_id,total:0,completed:0,failed:0}))).map(c=>{const p=c.total?100*c.completed/c.total:0;return `<div class="city"><div class="cityhead"><span>${esc(names[c.city_id]||c.city_id)}</span><span>${c.completed||0}/${c.total||0}</span></div><div class="mini"><div class="minifill" style="width:${p}%"></div></div>${c.failed?`<div class="error">失败 ${c.failed}</div>`:""}</div>`}).join("");
const events=(s.events||[]).map(e=>`${e.at||""}  ${e.message||""}`);const lines=[...events,...(s.log_tail||[])];$("log").textContent=lines.length?lines.slice(-100).join("\n"):"等待开始…";$("log").scrollTop=$("log").scrollHeight}
async function refresh(){const r=await fetch("/api/status",{cache:"no-store"});render(await r.json())}
$("start").onclick=()=>action("/api/start",{download_threads:Number($("download").value),acquisition_concurrency:Number($("concurrency").value)}).catch(e=>alert(e.message));$("pause").onclick=()=>action("/api/pause",{}).catch(e=>alert(e.message));refresh().catch(()=>{$("state").textContent="连接失败"});setInterval(()=>refresh().catch(()=>{}),1000);
</script></body></html>"""


class _DashboardHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    supervisor: PortableSentinelSupervisor
    control_token: str


class _Handler(BaseHTTPRequestHandler):
    server: _DashboardHTTPServer

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _send_json(self, payload: Mapping[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        if self.path in {"/", "/index.html"}:
            data = _PAGE.replace("__CONTROL_TOKEN__", self.server.control_token).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
        elif self.path == "/api/status":
            self._send_json(self.server.supervisor.snapshot())
        else:
            self._send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        if not hmac.compare_digest(
            self.headers.get("X-Sentinel-Control", ""), self.server.control_token
        ):
            self._send_json({"error": "forbidden"}, HTTPStatus.FORBIDDEN)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, UnicodeError, json.JSONDecodeError):
            self._send_json({"error": "invalid_json"}, HTTPStatus.BAD_REQUEST)
            return
        if not isinstance(body, dict):
            self._send_json({"error": "invalid_payload"}, HTTPStatus.BAD_REQUEST)
            return
        if self.path == "/api/start":
            try:
                payload = self.server.supervisor.start_or_continue(
                    download_threads=body.get("download_threads", DEFAULT_DOWNLOAD_THREADS),
                    acquisition_concurrency=body.get(
                        "acquisition_concurrency", DEFAULT_ACQUISITION_CONCURRENCY
                    ),
                )
            except ValueError as error:
                self._send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
                return
            self._send_json(payload)
        elif self.path == "/api/pause":
            self._send_json(self.server.supervisor.request_pause())
        else:
            self._send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)


def create_server(
    *, host: str, port: int, supervisor: PortableSentinelSupervisor
) -> _DashboardHTTPServer:
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("Sentinel dashboard must bind to localhost")
    server = _DashboardHTTPServer((host, port), _Handler)
    server.supervisor = supervisor
    server.control_token = secrets.token_urlsafe(32)
    return server


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=0.5)
    parser.add_argument("--initial-backoff-seconds", type=float, default=2.0)
    parser.add_argument("--maximum-backoff-seconds", type=float, default=300.0)
    arguments = parser.parse_args(argv)
    project_root = Path(__file__).resolve().parents[3]
    runtime = project_root / RUNTIME_RELATIVE_DIRECTORY
    try:
        with DashboardProcessLock(runtime / "dashboard.lock"):
            supervisor = PortableSentinelSupervisor(
                project_root,
                poll_seconds=arguments.poll_seconds,
                initial_backoff_seconds=arguments.initial_backoff_seconds,
                maximum_backoff_seconds=arguments.maximum_backoff_seconds,
            )
            server = create_server(host=arguments.host, port=arguments.port, supervisor=supervisor)
            url = f"http://{arguments.host}:{server.server_address[1]}/"
            print(f"Sentinel dashboard: {url}", flush=True)
            supervisor.begin_supervision()
            if not arguments.no_browser:
                webbrowser.open(url)
            try:
                server.serve_forever(poll_interval=0.5)
            except KeyboardInterrupt:
                print("Safe pause requested.", flush=True)
            finally:
                supervisor.close(request_pause=True)
                server.server_close()
    except DashboardAlreadyRunningError as error:
        print(str(error), flush=True)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
