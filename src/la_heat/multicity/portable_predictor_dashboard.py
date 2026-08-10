"""Local UI and resilient supervisor for the portable predictor component build.

The dashboard is orchestration only.  The scientific engine owns its caches,
checkpoints, and ``status.json``.  This module launches exactly
``scripts/build_portable_predictor_components.py``, persists run/pause intent,
and requests cooperative pause through ``PAUSE_REQUESTED``.
"""

from __future__ import annotations

import argparse
import hmac
import json
import math
import os
import re
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
DEFAULT_PORT: Final = 8768
ENGINE_RELATIVE_PATH: Final = Path("scripts/build_portable_predictor_components.py")
RUNTIME_RELATIVE_DIRECTORY: Final = Path(
    "data/interim/multicity/portable_predictors/runtime"
)
STATUS_FILENAME: Final = "status.json"
CONTROL_FILENAME: Final = "control.json"
PAUSE_FILENAME: Final = "PAUSE_REQUESTED"
CONTROL_SCHEMA_VERSION: Final = 1
DASHBOARD_VERSION: Final = "portable-predictor-dashboard-v1"
EARTHDATA_ENVIRONMENT_VARIABLE: Final = "EARTHDATA_TOKEN"
EARTHDATA_ENVIRONMENT_VARIABLES: Final = (
    "EARTHDATA_TOKEN",
    "NASA_EARTHDATA_TOKEN",
    "EDL_TOKEN",
)
WAITING_TOKEN_STATES: Final = frozenset(
    {
        "awaiting_earthdata_token",
        "earthdata_token_required",
        "waiting_for_earthdata_token",
        "waiting_for_token",
    }
)
COMPLETE_STATES: Final = frozenset({"complete", "completed"})
BLOCKED_STATES: Final = frozenset(
    {
        "blocked",
        "error",
        "failed",
        "failed_scientific",
        "incomplete_with_failures",
    }
)
DEFAULT_INITIAL_BACKOFF_SECONDS: Final = 2.0
DEFAULT_MAXIMUM_BACKOFF_SECONDS: Final = 300.0
DEFAULT_STABLE_RUNTIME_SECONDS: Final = 600.0

_SENSITIVE_URL_QUERY = re.compile(r"(https?://[^\s?]+)\?[^\s]+", re.IGNORECASE)
_BEARER_VALUE = re.compile(r"\bBearer\s+[^\s,;]+", re.IGNORECASE)
_TOKEN_ASSIGNMENT = re.compile(
    r"\b(access[_-]?token|authorization|token)\b\s*[:=]\s*[^\s,;]+",
    re.IGNORECASE,
)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _positive_seconds(value: float, *, label: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{label} must be finite and greater than zero.")
    return number


def _atomic_json(payload: Mapping[str, Any], path: Path) -> None:
    """Write one small runtime control document without exposing partial bytes."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.{secrets.token_hex(4)}.tmp"
    )
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _safe_text(
    value: object,
    *,
    limit: int = 500,
    secrets_to_redact: Sequence[str] = (),
) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    for secret in secrets_to_redact:
        if secret:
            text = text.replace(secret, "<redacted>")
    text = _SENSITIVE_URL_QUERY.sub(r"\1?<redacted>", text)
    text = _BEARER_VALUE.sub("Bearer <redacted>", text)
    text = _TOKEN_ASSIGNMENT.sub(r"\1=<redacted>", text)
    return text[:limit]


def _nonnegative_count(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, (list, tuple, set, dict)):
        return len(value)
    return 0


def _finite_nonnegative_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0:
        return None
    return number


def _current_items(
    value: object,
    *,
    secrets_to_redact: Sequence[str] = (),
) -> list[str]:
    values = value if isinstance(value, list | tuple) else [value]
    current: list[str] = []
    for item in values:
        if item is None:
            continue
        if isinstance(item, dict):
            selected = next(
                (
                    item[key]
                    for key in ("task_id", "city_id", "id", "item", "name")
                    if key in item
                ),
                None,
            )
            if selected is not None:
                current.append(
                    _safe_text(
                        selected,
                        limit=180,
                        secrets_to_redact=secrets_to_redact,
                    )
                )
        else:
            current.append(
                _safe_text(item, limit=180, secrets_to_redact=secrets_to_redact)
            )
    return current[:12]


def _events(
    value: object,
    *,
    secrets_to_redact: Sequence[str] = (),
) -> list[dict[str, str]]:
    values = value if isinstance(value, list | tuple) else [value]
    result: list[dict[str, str]] = []
    for item in values:
        if item is None:
            continue
        if isinstance(item, dict):
            at = item.get("at", item.get("time", ""))
            message = item.get("message", item.get("event", item.get("type")))
        else:
            at = ""
            message = item
        if message is None:
            continue
        result.append(
            {
                "at": _safe_text(
                    at,
                    limit=80,
                    secrets_to_redact=secrets_to_redact,
                ),
                "message": _safe_text(
                    message,
                    limit=500,
                    secrets_to_redact=secrets_to_redact,
                ),
            }
        )
    return result[-40:]


def _clean_error(
    value: object,
    *,
    secrets_to_redact: Sequence[str] = (),
) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        error_type = value.get("error_type", value.get("type", "Error"))
        message = value.get("message")
        retryable = value.get("retryable")
    else:
        error_type = type(value).__name__ if not isinstance(value, str) else "Error"
        message = value
        retryable = None
    result: dict[str, Any] = {
        "type": _safe_text(
            error_type,
            limit=120,
            secrets_to_redact=secrets_to_redact,
        )
    }
    if message is not None:
        result["message"] = _safe_text(
            message,
            limit=500,
            secrets_to_redact=secrets_to_redact,
        )
    if isinstance(retryable, bool):
        result["retryable"] = retryable
    return result


def empty_engine_status() -> dict[str, Any]:
    """Return the status visible before the scientific engine publishes one."""

    return {
        "state": "not_started",
        "phase": "准备",
        "current_city": None,
        "total": 0,
        "completed": 0,
        "pending": 0,
        "running": 0,
        "failed": 0,
        "current": [],
        "eta_seconds": None,
        "events": [],
        "error": None,
        "updated_at_utc": None,
        "status_contract_error": None,
    }


def normalize_engine_status(
    payload: object,
    *,
    secrets_to_redact: Sequence[str] = (),
) -> dict[str, Any]:
    """Normalize the small version-tolerant engine-to-dashboard contract."""

    result = empty_engine_status()
    if not isinstance(payload, dict):
        result["status_contract_error"] = "status JSON is not an object"
        return result

    state = payload.get("state")
    if isinstance(state, str) and state.strip():
        result["state"] = _safe_text(
            state,
            limit=100,
            secrets_to_redact=secrets_to_redact,
        )
    phase = payload.get("phase", payload.get("stage"))
    if isinstance(phase, str) and phase.strip():
        result["phase"] = _safe_text(
            phase,
            limit=160,
            secrets_to_redact=secrets_to_redact,
        )
    current_value = payload.get(
        "current",
        payload.get("active_tasks", payload.get("current_task", [])),
    )
    city = payload.get("current_city", payload.get("city_id", payload.get("city")))
    if city is None and isinstance(current_value, dict):
        city = current_value.get(
            "city_id",
            current_value.get("current_city", current_value.get("city")),
        )
    if city is not None:
        result["current_city"] = _safe_text(
            city,
            limit=160,
            secrets_to_redact=secrets_to_redact,
        )

    total = _nonnegative_count(payload.get("total", payload.get("total_tasks", 0)))
    completed = _nonnegative_count(
        payload.get("completed", payload.get("completed_tasks", 0))
    )
    running = _nonnegative_count(
        payload.get("running", payload.get("active", payload.get("active_tasks", 0)))
    )
    failed = _nonnegative_count(payload.get("failed", payload.get("failures", 0)))
    pending_value = payload.get("pending")
    pending = (
        _nonnegative_count(pending_value)
        if pending_value is not None
        else max(0, total - completed - running)
    )
    if total and completed > total:
        result["status_contract_error"] = "completed exceeds total"
        completed = total
    result.update(
        {
            "total": total,
            "completed": completed,
            "pending": pending,
            "running": running,
            "failed": failed,
        }
    )

    result["current"] = _current_items(
        current_value,
        secrets_to_redact=secrets_to_redact,
    )
    result["eta_seconds"] = _finite_nonnegative_number(
        payload.get("eta_seconds", payload.get("estimated_remaining_seconds"))
    )
    result["events"] = _events(
        payload.get("events", payload.get("log_tail", payload.get("logs", []))),
        secrets_to_redact=secrets_to_redact,
    )
    result["error"] = _clean_error(
        payload.get("error"),
        secrets_to_redact=secrets_to_redact,
    )
    updated = payload.get("updated_at_utc", payload.get("updated_at"))
    if isinstance(updated, str):
        result["updated_at_utc"] = _safe_text(
            updated,
            limit=100,
            secrets_to_redact=secrets_to_redact,
        )
    return result


def read_engine_status(
    path: str | Path,
    *,
    secrets_to_redact: Sequence[str] = (),
) -> dict[str, Any]:
    """Read one atomic status document without raising into the HTTP server."""

    status_path = Path(path)
    if not status_path.is_file():
        return empty_engine_status()
    try:
        before = status_path.stat()
        payload = json.loads(status_path.read_text(encoding="utf-8"))
        after = status_path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise RuntimeError("status changed during read")
    except (OSError, UnicodeError, json.JSONDecodeError, RuntimeError):
        status = empty_engine_status()
        status["status_contract_error"] = "status JSON could not be read"
        return status
    return normalize_engine_status(payload, secrets_to_redact=secrets_to_redact)


def _normalized_state(status: Mapping[str, Any]) -> str:
    return str(status.get("state", "")).strip().lower()


def _waiting_for_earthdata_token(status: Mapping[str, Any]) -> bool:
    state = _normalized_state(status)
    if state in WAITING_TOKEN_STATES:
        return True
    combined = f"{state} {status.get('phase', '')}".lower()
    return "earthdata" in combined and "token" in combined and (
        "wait" in combined or "required" in combined or "await" in combined
    )


def _complete(status: Mapping[str, Any]) -> bool:
    return _normalized_state(status) in COMPLETE_STATES


def _blocked(status: Mapping[str, Any]) -> bool:
    if _normalized_state(status) not in BLOCKED_STATES:
        return False
    error = status.get("error")
    return not isinstance(error, dict) or error.get("retryable") is not True


def _validated_optional_token(value: object) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Earthdata token must be a non-empty string.")
    if value != value.strip() or any(character in value for character in "\0\r\n"):
        raise ValueError("Earthdata token contains invalid whitespace.")
    return value


def _environment_token(environment: Mapping[str, str]) -> tuple[str | None, bool]:
    available = [
        value
        for name in EARTHDATA_ENVIRONMENT_VARIABLES
        if (value := environment.get(name, ""))
    ]
    if len(available) != 1:
        return None, len(available) > 1
    try:
        return _validated_optional_token(available[0]), False
    except ValueError:
        return None, True


class DashboardControlStore:
    """Atomic persistence for start/pause intent; invalid bytes fail closed."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self._lock = threading.Lock()

    def _read_unlocked(self) -> dict[str, Any] | None:
        if not self.path.is_file():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _desired(payload: Mapping[str, Any] | None) -> str | None:
        if payload is None:
            return None
        desired = payload.get("desired_state")
        return str(desired) if desired in {"running", "paused"} else None

    def initialize(self, *, default_desired_state: str) -> str:
        if default_desired_state not in {"running", "paused"}:
            raise ValueError("default_desired_state must be running or paused")
        with self._lock:
            payload = self._read_unlocked()
            desired = self._desired(payload)
            if desired is not None:
                return desired
            # A missing file starts automatically. Corrupt existing bytes fail closed.
            desired = default_desired_state if payload is None else "paused"
            self._write_unlocked(desired)
            return desired

    def get_desired_state(self) -> str:
        with self._lock:
            return self._desired(self._read_unlocked()) or "paused"

    def _write_unlocked(self, desired_state: str) -> None:
        _atomic_json(
            {
                "schema_version": CONTROL_SCHEMA_VERSION,
                "desired_state": desired_state,
                "updated_at_utc": _utc_now(),
            },
            self.path,
        )

    def set_desired_state(self, desired_state: str) -> None:
        if desired_state not in {"running", "paused"}:
            raise ValueError("desired_state must be running or paused")
        with self._lock:
            self._write_unlocked(desired_state)


class DashboardAlreadyRunningError(RuntimeError):
    """Raised when another dashboard owns the runtime lock."""


class DashboardProcessLock:
    """OS-released single-instance lock for the dashboard server."""

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
            else:  # pragma: no cover - production currently runs on Windows.
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as error:
            stream.close()
            raise DashboardAlreadyRunningError(
                "The portable predictor dashboard is already running."
            ) from error
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
            else:  # pragma: no cover - production currently runs on Windows.
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()
            self._stream = None


class ManagedProcess(Protocol):
    """Only the exact child-process surface needed by the supervisor."""

    pid: int

    def poll(self) -> int | None:
        """Return the exit code, or ``None`` while the child is running."""


SpawnProcess = Callable[[tuple[str, ...], Path, Mapping[str, str]], ManagedProcess]


def build_engine_command(project_root: str | Path) -> tuple[str, ...]:
    """Return the sole command this dashboard is allowed to launch."""

    root = Path(project_root).resolve()
    engine = (root / ENGINE_RELATIVE_PATH).resolve()
    if engine.parent != (root / "scripts").resolve():
        raise RuntimeError("Portable predictor engine escaped the scripts directory.")
    return (sys.executable, str(engine))


def _spawn_engine(
    command: tuple[str, ...],
    cwd: Path,
    environment: Mapping[str, str],
) -> ManagedProcess:
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    return subprocess.Popen(  # noqa: S603 - fixed local argv, no shell
        command,
        cwd=cwd,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creation_flags,
    )


def _write_pause_marker(path: Path) -> None:
    _atomic_json(
        {
            "schema_version": CONTROL_SCHEMA_VERSION,
            "intent": "pause",
            "requested_at_utc": _utc_now(),
        },
        path,
    )


class PortablePredictorSupervisor:
    """Persist intent and supervise exactly one portable-component engine child."""

    def __init__(
        self,
        project_root: str | Path,
        *,
        spawn: SpawnProcess = _spawn_engine,
        poll_seconds: float = 0.5,
        initial_backoff_seconds: float = DEFAULT_INITIAL_BACKOFF_SECONDS,
        maximum_backoff_seconds: float = DEFAULT_MAXIMUM_BACKOFF_SECONDS,
        stable_runtime_seconds: float = DEFAULT_STABLE_RUNTIME_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
        parent_environment: Mapping[str, str] | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.runtime_directory = self.project_root / RUNTIME_RELATIVE_DIRECTORY
        self.runtime_directory.mkdir(parents=True, exist_ok=True)
        self.temporary_directory = self.runtime_directory / "tmp"
        self.temporary_directory.mkdir(parents=True, exist_ok=True)
        self.status_path = self.runtime_directory / STATUS_FILENAME
        self.pause_path = self.runtime_directory / PAUSE_FILENAME
        self.control = DashboardControlStore(
            self.runtime_directory / CONTROL_FILENAME
        )
        self.control.initialize(
            default_desired_state="paused" if self.pause_path.exists() else "running"
        )

        self._spawn = spawn
        self._poll_seconds = _positive_seconds(poll_seconds, label="poll_seconds")
        self._initial_backoff_seconds = _positive_seconds(
            initial_backoff_seconds,
            label="initial_backoff_seconds",
        )
        self._maximum_backoff_seconds = _positive_seconds(
            maximum_backoff_seconds,
            label="maximum_backoff_seconds",
        )
        if self._maximum_backoff_seconds < self._initial_backoff_seconds:
            raise ValueError("maximum_backoff_seconds cannot be below the initial value")
        self._stable_runtime_seconds = _positive_seconds(
            stable_runtime_seconds,
            label="stable_runtime_seconds",
        )
        self._monotonic = monotonic
        self._parent_environment = dict(
            os.environ if parent_environment is None else parent_environment
        )
        initial_token, ambiguous_token = _environment_token(self._parent_environment)

        self._condition = threading.Condition(threading.RLock())
        self._child: ManagedProcess | None = None
        self._child_started_monotonic: float | None = None
        self._completed_at_launch = 0
        self._child_token_version = 0
        self._earthdata_token = initial_token
        self._token_version = 1 if initial_token is not None else 0
        self._closed = False
        self._monitor: threading.Thread | None = None
        self._failure_count = 0
        self._automatic_restart_count = 0
        self._next_launch_monotonic: float | None = None
        self._manual_launch_requested = False
        self._last_valid_status: dict[str, Any] | None = None
        self._events: deque[dict[str, str]] = deque(maxlen=60)
        if ambiguous_token:
            self._event(
                "检测到多个或无效的 Earthdata 环境变量；需要在页面重新输入 token。"
            )

    def _event(self, message: str) -> None:
        self._events.append({"at": _utc_now(), "message": message[:500]})

    def _secrets(self) -> tuple[str, ...]:
        return () if self._earthdata_token is None else (self._earthdata_token,)

    def _engine_status_locked(self) -> dict[str, Any]:
        status = read_engine_status(
            self.status_path,
            secrets_to_redact=self._secrets(),
        )
        if status["status_contract_error"] is None:
            self._last_valid_status = dict(status)
            return status
        if self._last_valid_status is None:
            return status
        retained = dict(self._last_valid_status)
        retained["status_contract_error"] = status["status_contract_error"]
        return retained

    def _build_child_environment_locked(self) -> dict[str, str]:
        environment = dict(self._parent_environment)
        for name in EARTHDATA_ENVIRONMENT_VARIABLES:
            environment.pop(name, None)
        temporary = str(self.temporary_directory.resolve())
        environment.update({"TMP": temporary, "TEMP": temporary, "TMPDIR": temporary})
        if self._earthdata_token is not None:
            environment[EARTHDATA_ENVIRONMENT_VARIABLE] = self._earthdata_token
        return environment

    def _schedule_restart_locked(self, status: Mapping[str, Any]) -> None:
        runtime = (
            0.0
            if self._child_started_monotonic is None
            else max(0.0, self._monotonic() - self._child_started_monotonic)
        )
        if (
            _nonnegative_count(status.get("completed")) > self._completed_at_launch
            or runtime >= self._stable_runtime_seconds
        ):
            self._failure_count = 0
        self._failure_count += 1
        delay = min(
            self._initial_backoff_seconds * (2 ** min(self._failure_count - 1, 30)),
            self._maximum_backoff_seconds,
        )
        self._automatic_restart_count += 1
        self._next_launch_monotonic = self._monotonic() + delay
        self._event(f"engine 异常退出；{delay:g} 秒后自动重启。")

    def _launch_locked(self, status: Mapping[str, Any]) -> None:
        if self._closed or self._child is not None or self.pause_path.exists():
            return
        if self.control.get_desired_state() != "running":
            return
        command = build_engine_command(self.project_root)
        environment = self._build_child_environment_locked()
        token_version = self._token_version if self._earthdata_token is not None else 0
        try:
            child = self._spawn(command, self.project_root, environment)
        except Exception as error:  # noqa: BLE001 - only the exception type is retained
            self._event(f"engine 启动失败（{type(error).__name__}）。")
            self._child_started_monotonic = None
            self._schedule_restart_locked(status)
        else:
            self._child = child
            self._child_started_monotonic = self._monotonic()
            self._completed_at_launch = _nonnegative_count(status.get("completed"))
            self._child_token_version = token_version
            self._next_launch_monotonic = None
            self._manual_launch_requested = False
            self._event("static + calendar + Daymet engine 已启动。")
        finally:
            if EARTHDATA_ENVIRONMENT_VARIABLE in environment:
                environment[EARTHDATA_ENVIRONMENT_VARIABLE] = ""
            environment.clear()

    def _handle_exit_locked(self, exit_code: int) -> None:
        self._child = None
        status = self._engine_status_locked()
        desired = self.control.get_desired_state()
        if desired == "paused" or self.pause_path.exists():
            self._next_launch_monotonic = None
            self._event("engine 已在安全暂停边界停止。")
        elif _complete(status):
            self.control.set_desired_state("paused")
            self._earthdata_token = None
            self._next_launch_monotonic = None
            self._failure_count = 0
            self._event("static、calendar 与 Daymet 组件已完成。")
        elif _waiting_for_earthdata_token(status):
            if (
                self._earthdata_token is not None
                and self._token_version > self._child_token_version
            ):
                self._next_launch_monotonic = self._monotonic()
                self._event("已收到新的 Earthdata token；准备继续。")
            else:
                if self._child_token_version:
                    self._earthdata_token = None
                self._next_launch_monotonic = None
                self._event("等待 Earthdata token；不会自动重启。")
        elif _blocked(status):
            self._earthdata_token = None
            self._next_launch_monotonic = None
            self._event("engine 报告不可自动恢复的错误；请检查后手动继续。")
        else:
            self._event(f"engine 退出（code {int(exit_code)}）。")
            self._schedule_restart_locked(status)
        self._child_started_monotonic = None
        self._condition.notify_all()

    def _monitor_loop(self) -> None:
        while True:
            with self._condition:
                if self._closed:
                    return
                status = self._engine_status_locked()
                child = self._child
                if child is not None:
                    exit_code = child.poll()
                    if exit_code is not None:
                        self._handle_exit_locked(exit_code)
                        continue
                else:
                    desired = self.control.get_desired_state()
                    if self.pause_path.exists() and desired == "running":
                        self.control.set_desired_state("paused")
                        desired = "paused"
                    if _complete(status) and desired == "running":
                        self.control.set_desired_state("paused")
                        desired = "paused"
                        self._earthdata_token = None
                    blocked_without_retry = (
                        _blocked(status)
                        and not self._manual_launch_requested
                        and self._next_launch_monotonic is None
                    )
                    waiting_without_token = (
                        _waiting_for_earthdata_token(status)
                        and self._earthdata_token is None
                    )
                    if (
                        desired == "running"
                        and not self.pause_path.exists()
                        and not _complete(status)
                        and not blocked_without_retry
                        and not waiting_without_token
                    ):
                        if (
                            self._next_launch_monotonic is None
                            or self._monotonic() >= self._next_launch_monotonic
                        ):
                            self._launch_locked(status)
                            continue

                wait_seconds = self._poll_seconds
                if self._next_launch_monotonic is not None:
                    wait_seconds = min(
                        wait_seconds,
                        max(0.01, self._next_launch_monotonic - self._monotonic()),
                    )
                self._condition.wait(timeout=wait_seconds)

    def begin_supervision(self) -> None:
        """Start the monitor; a fresh runtime automatically launches the engine."""

        with self._condition:
            if self._monitor is not None and self._monitor.is_alive():
                return
            self._closed = False
            self._monitor = threading.Thread(
                target=self._monitor_loop,
                name="portable-predictor-dashboard-supervisor",
                daemon=True,
            )
            self._monitor.start()

    def start_or_continue(self, earthdata_token: object = None) -> dict[str, Any]:
        """Clear persistent pause and launch, optionally with an in-memory token."""

        token = _validated_optional_token(earthdata_token)
        self.begin_supervision()
        with self._condition:
            if self._closed:
                return self._snapshot_locked()
            if token is not None:
                self._earthdata_token = token
                self._token_version += 1
                self._event("已接收内存中的 Earthdata token。")
            self.pause_path.unlink(missing_ok=True)
            self.control.set_desired_state("running")
            self._failure_count = 0
            self._next_launch_monotonic = None
            self._manual_launch_requested = True
            status = self._engine_status_locked()
            if _waiting_for_earthdata_token(status) and self._earthdata_token is None:
                self._manual_launch_requested = False
                self._event("继续运行需要 Earthdata token。")
            elif self._child is None:
                self._launch_locked(status)
            self._condition.notify_all()
            return self._snapshot_locked()

    def request_pause(self) -> dict[str, Any]:
        """Persist pause and let the exact child stop at its next atomic boundary."""

        with self._condition:
            _write_pause_marker(self.pause_path)
            self.control.set_desired_state("paused")
            self._earthdata_token = None
            self._token_version += 1
            self._next_launch_monotonic = None
            self._manual_launch_requested = False
            self._event("已请求安全暂停；当前原子任务完成后停止。")
            self._condition.notify_all()
            return self._snapshot_locked()

    def _snapshot_locked(self) -> dict[str, Any]:
        engine = self._engine_status_locked()
        desired = self.control.get_desired_state()
        child_alive = self._child is not None and self._child.poll() is None
        pause_requested = self.pause_path.exists() or desired == "paused"
        waiting = _waiting_for_earthdata_token(engine)
        state = engine["state"]
        if _complete(engine):
            state = "complete"
        elif waiting:
            state = "waiting_for_earthdata_token"
        elif pause_requested:
            state = "pausing" if child_alive else "paused"
        elif child_alive:
            state = "running"
        elif _blocked(engine):
            state = engine["state"]
        elif self._next_launch_monotonic is not None:
            state = "restarting"
        elif desired == "running":
            state = "starting"

        restart_in = None
        if self._next_launch_monotonic is not None:
            restart_in = max(0.0, self._next_launch_monotonic - self._monotonic())
        total = int(engine["total"])
        completed = int(engine["completed"])
        progress_fraction = 0.0 if total <= 0 else min(1.0, completed / total)
        supervisor_events = list(self._events)[-20:]
        return {
            **engine,
            "state": state,
            "desired_state": desired,
            "progress_fraction": progress_fraction,
            "pause_requested": pause_requested,
            "token_required": waiting,
            "earthdata_token_in_memory": self._earthdata_token is not None,
            "engine_process_running": child_alive,
            "managed_pid": None if self._child is None else self._child.pid,
            "automatic_restart_count": self._automatic_restart_count,
            "consecutive_process_failures": self._failure_count,
            "restart_in_seconds": restart_in,
            "events": (engine["events"] + supervisor_events)[-30:],
            "dashboard_version": DASHBOARD_VERSION,
        }

    def snapshot(self) -> dict[str, Any]:
        with self._condition:
            return self._snapshot_locked()

    def close(self, *, request_pause: bool = False) -> None:
        """Stop supervising; optionally persist a cooperative pause first."""

        if request_pause:
            self.request_pause()
        with self._condition:
            self._earthdata_token = None
            self._closed = True
            self._condition.notify_all()
            monitor = self._monitor
        if monitor is not None and monitor is not threading.current_thread():
            monitor.join(timeout=max(2.0, self._poll_seconds * 2))


_PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>四城市预测变量构建</title>
  <style>
    :root{color-scheme:dark;font-family:system-ui,-apple-system,"Segoe UI",sans-serif}
    body{margin:0;background:#111;color:#eee}main{max-width:920px;margin:26px auto;padding:0 16px}
    header,.controls,.metrics,.line{display:flex;gap:12px;align-items:center;flex-wrap:wrap}
    header{justify-content:space-between}
    .state{padding:6px 11px;border:1px solid #555;border-radius:999px}
    .controls{margin:20px 0}.controls input{flex:1 1 320px;min-width:220px}
    input,button{font:inherit;padding:9px 12px;border:1px solid #555;border-radius:7px}
    button{cursor:pointer;font-weight:650}.start{background:#287ee7;color:#fff}.pause{background:#444;color:#fff}
    button:disabled{opacity:.45;cursor:default}progress{width:100%;height:18px;accent-color:#287ee7}
    .metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));margin:16px 0}
    .card{background:#1c1c1c;border:1px solid #383838;border-radius:9px;padding:12px}
    .value{display:block;margin-top:4px;font-size:1.25rem;font-weight:700;overflow-wrap:anywhere}
    .muted{color:#aaa}.warning{color:#ffd46a}pre{background:#181818;padding:12px;border-radius:8px;max-height:280px;overflow:auto;white-space:pre-wrap}
  </style>
</head>
<body><main>
  <header>
    <div>
      <h1>四城市预测变量构建</h1>
      <div class="muted">static + calendar + Daymet</div>
    </div>
    <span id="state" class="state">连接中</span>
  </header>
  <div class="controls">
    <input id="token" type="password" autocomplete="off"
      placeholder="Earthdata token（仅保存在内存）">
    <button id="start" class="start" type="button">开始 / 继续</button>
    <button id="pause" class="pause" type="button">安全暂停</button>
  </div>
  <div class="line">
    <span id="progress-label">0 / 0</span>
    <span id="warning" class="warning"></span>
  </div>
  <progress id="progress" max="1" value="0"></progress>
  <div class="metrics">
    <div class="card">阶段<span id="phase" class="value">准备</span></div>
    <div class="card">城市<span id="city" class="value">—</span></div>
    <div class="card">完成<span id="completed" class="value">0 / 0</span></div>
    <div class="card">预计剩余<span id="eta" class="value">估算中</span></div>
    <div class="card">自动重启<span id="restarts" class="value">0</span></div>
  </div>
  <div>当前任务：<span id="current">—</span></div>
  <h2>最近日志</h2><pre id="events">等待 engine 状态…</pre>
</main>
<script>
const control = "__CONTROL_TOKEN__";
const byId = id => document.getElementById(id);
const names = {
  not_started: "尚未开始",
  starting: "正在启动",
  running: "运行中",
  restarting: "自动重启中",
  pausing: "正在安全暂停",
  paused: "已暂停",
  waiting_for_earthdata_token: "等待 Earthdata token",
  complete: "已完成",
  blocked: "已阻塞",
  failed: "失败",
  error: "错误"
};
function duration(value) {
  if (value == null || !Number.isFinite(value)) return "估算中";
  value = Math.max(0, Math.round(value));
  if (value < 60) return `${value} 秒`;
  const minutes = Math.round(value / 60);
  if (minutes < 60) return `${minutes} 分钟`;
  return `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分钟`;
}
function render(data) {
  const total = Number(data.total || 0);
  const done = Number(data.completed || 0);
  byId("state").textContent = names[data.state] || data.state || "未知";
  byId("phase").textContent = data.phase || "准备";
  byId("city").textContent = data.current_city || "—";
  byId("completed").textContent =
    `${done.toLocaleString()} / ${total.toLocaleString()}`;
  byId("eta").textContent = duration(data.eta_seconds);
  byId("restarts").textContent =
    Number(data.automatic_restart_count || 0).toLocaleString();
  byId("progress").max = Math.max(1, total);
  byId("progress").value = done;
  const percent = total ? 100 * done / total : 0;
  byId("progress-label").textContent =
    `${done.toLocaleString()} / ${total.toLocaleString()}（${percent.toFixed(1)}%）`;
  byId("current").textContent = (data.current || []).join("，") || "—";
  byId("warning").textContent = data.token_required
    ? "请输入 token 后点击继续"
    : (data.status_contract_error || data.error?.message || "");
  const events = Array.isArray(data.events) ? data.events : [];
  byId("events").textContent = events.length
    ? events.map(item => `${item.at || ""}  ${item.message || ""}`).join("\n")
    : "暂无日志";
  byId("start").disabled = ["running", "pausing", "complete"].includes(data.state);
  byId("pause").disabled =
    !["starting", "running", "restarting"].includes(data.state);
}
async function refresh() {
  const response = await fetch("/api/status", {cache: "no-store"});
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  render(await response.json());
}
async function action(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Portable-Predictor-Control": control
    },
    body: JSON.stringify(body)
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
  render(payload);
}
byId("start").onclick = async () => {
  let token = byId("token").value;
  byId("token").value = "";
  try {
    await action("/api/start", {earthdata_token: token});
  } finally {
    token = "";
  }
};
byId("pause").onclick = () => action("/api/pause", {});
refresh().catch(() => {byId("state").textContent = "连接失败";});
setInterval(() => refresh().catch(() => {}), 1000);
</script></body></html>
"""


class _DashboardHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    supervisor: PortablePredictorSupervisor
    control_token: str


class _Handler(BaseHTTPRequestHandler):
    server: _DashboardHTTPServer

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _send_json(
        self,
        payload: Mapping[str, Any],
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path in {"/", "/index.html"}:
            page = _PAGE.replace("__CONTROL_TOKEN__", self.server.control_token)
            data = page.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; style-src 'unsafe-inline'; "
                "script-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(data)
            return
        if self.path == "/api/status":
            self._send_json(self.server.supervisor.snapshot())
            return
        self._send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        supplied = self.headers.get("X-Portable-Predictor-Control", "")
        if not hmac.compare_digest(supplied, self.server.control_token):
            self._send_json({"error": "forbidden"}, HTTPStatus.FORBIDDEN)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json({"error": "invalid_content_length"}, HTTPStatus.BAD_REQUEST)
            return
        if length < 0 or length > 16_384:
            self._send_json({"error": "request_too_large"}, HTTPStatus.BAD_REQUEST)
            return
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except (UnicodeError, json.JSONDecodeError):
            self._send_json({"error": "invalid_json"}, HTTPStatus.BAD_REQUEST)
            return
        if not isinstance(body, dict):
            self._send_json({"error": "invalid_payload"}, HTTPStatus.BAD_REQUEST)
            return

        if self.path == "/api/start":
            token = body.get("earthdata_token")
            try:
                payload = self.server.supervisor.start_or_continue(token)
            except ValueError as error:
                self._send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
                return
            finally:
                token = None
                body = {}
            self._send_json(payload)
            return
        if self.path == "/api/pause":
            self._send_json(self.server.supervisor.request_pause())
            return
        self._send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)


def create_server(
    *,
    host: str,
    port: int,
    supervisor: PortablePredictorSupervisor,
) -> _DashboardHTTPServer:
    """Create a token-protected loopback HTTP server."""

    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("Portable predictor dashboard must bind to localhost.")
    if not 0 <= port <= 65_535:
        raise ValueError("port must be between 0 and 65535")
    server = _DashboardHTTPServer((host, port), _Handler)
    server.supervisor = supervisor
    server.control_token = secrets.token_urlsafe(32)
    return server


def main(argv: Sequence[str] | None = None) -> int:
    """Run the auto-starting portable predictor dashboard."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=0.5)
    parser.add_argument(
        "--initial-backoff-seconds",
        type=float,
        default=DEFAULT_INITIAL_BACKOFF_SECONDS,
    )
    parser.add_argument(
        "--maximum-backoff-seconds",
        type=float,
        default=DEFAULT_MAXIMUM_BACKOFF_SECONDS,
    )
    arguments = parser.parse_args(argv)

    project_root = Path(__file__).resolve().parents[3]
    runtime_directory = project_root / RUNTIME_RELATIVE_DIRECTORY
    try:
        with DashboardProcessLock(runtime_directory / "dashboard.lock"):
            supervisor = PortablePredictorSupervisor(
                project_root,
                poll_seconds=arguments.poll_seconds,
                initial_backoff_seconds=arguments.initial_backoff_seconds,
                maximum_backoff_seconds=arguments.maximum_backoff_seconds,
            )
            server = create_server(
                host=arguments.host,
                port=arguments.port,
                supervisor=supervisor,
            )
            url = f"http://{arguments.host}:{server.server_address[1]}/"
            print(f"Portable predictor dashboard: {url}", flush=True)
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
