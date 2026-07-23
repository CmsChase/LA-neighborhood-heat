"""Local HTTP dashboard and resilient supervisor for grouped model evaluation."""

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
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Final, Protocol

from la_heat.execution_ownership import (
    ExecutionTransferredOutError,
    assert_grouped_execution_authorized,
)
from la_heat.model_run_queue import ModelRunQueue, RunNotFoundError

DEFAULT_RUN_DIRECTORY: Final = Path("data/interim/model_runs")
DEFAULT_QUEUE_FILENAME: Final = "model_tasks.sqlite3"
DEFAULT_STATUS_FILENAME: Final = "status.json"
DEFAULT_CONTROL_FILENAME: Final = "dashboard_control.json"
CONTROL_SCHEMA_VERSION: Final = 1


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _positive_seconds(value: float, *, label: str) -> float:
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{label} must be finite and greater than zero.")
    return result


def _atomic_json(payload: dict[str, Any], path: Path) -> None:
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


class CoordinatorProcess(Protocol):
    """Small subprocess interface required by the supervisor."""

    pid: int

    def poll(self) -> int | None:
        """Return ``None`` while running, otherwise the process exit code."""


SpawnCoordinator = Callable[[tuple[str, ...], Path], CoordinatorProcess]


class DashboardAlreadyRunningError(RuntimeError):
    """Raised when another dashboard owns the local process lock."""


class WorkerChangeError(RuntimeError):
    """Raised when worker concurrency is changed outside a safe paused state."""


class DashboardProcessLock:
    """OS-released single-byte lock preventing duplicate dashboard servers."""

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
        except (OSError, BlockingIOError) as error:
            stream.close()
            raise DashboardAlreadyRunningError(
                "Another model dashboard already owns the server lock."
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
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()
            self._stream = None


class DashboardControlStore:
    """Atomic persistence for dashboard start/pause intent."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self._lock = threading.Lock()

    def _read_unlocked(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def get_desired_state(self) -> str:
        with self._lock:
            payload = self._read_unlocked()
            desired = payload.get("desired_state")
            return str(desired) if desired in {"running", "paused"} else "paused"

    def get_workers(self) -> int | None:
        with self._lock:
            value = self._read_unlocked().get("workers")
            if isinstance(value, bool) or not isinstance(value, int):
                return None
            return value if 1 <= value <= 8 else None

    def set_desired_state(self, desired_state: str) -> None:
        if desired_state not in {"running", "paused"}:
            raise ValueError("desired_state must be 'running' or 'paused'.")
        with self._lock:
            existing = self._read_unlocked()
            payload: dict[str, Any] = {
                "schema_version": CONTROL_SCHEMA_VERSION,
                "desired_state": desired_state,
                "updated_at_utc": _utc_now(),
            }
            workers = existing.get("workers")
            if isinstance(workers, int) and not isinstance(workers, bool) and 1 <= workers <= 8:
                payload["workers"] = workers
            _atomic_json(
                payload,
                self.path,
            )

    def set_workers(self, workers: int) -> None:
        if isinstance(workers, bool) or not isinstance(workers, int) or not 1 <= workers <= 8:
            raise ValueError("workers must be an integer between 1 and 8.")
        with self._lock:
            existing = self._read_unlocked()
            desired = existing.get("desired_state")
            if desired not in {"running", "paused"}:
                desired = "paused"
            _atomic_json(
                {
                    "schema_version": CONTROL_SCHEMA_VERSION,
                    "desired_state": desired,
                    "workers": workers,
                    "updated_at_utc": _utc_now(),
                },
                self.path,
            )


def build_coordinator_command(
    project_root: str | Path,
    *,
    workers: int,
    coordinator_script: str | Path = "scripts/run_grouped_models.py",
) -> tuple[str, ...]:
    """Build the default grouped-coordinator command without shell parsing."""

    if isinstance(workers, bool) or not 1 <= int(workers) <= 8:
        raise ValueError("workers must be between 1 and 8.")
    root = Path(project_root).resolve()
    script = Path(coordinator_script)
    if not script.is_absolute():
        script = root / script
    return (sys.executable, str(script.resolve()), "--workers", str(int(workers)))


def _spawn_coordinator(command: tuple[str, ...], cwd: Path) -> CoordinatorProcess:
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    return subprocess.Popen(  # noqa: S603 - argv is explicit and shell is disabled
        command,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creation_flags,
    )


def _backoff_seconds(
    failure_count: int,
    *,
    initial_seconds: float,
    maximum_seconds: float,
) -> float:
    exponent = min(max(0, failure_count - 1), 62)
    return min(initial_seconds * (2**exponent), maximum_seconds)


class ModelDashboardSupervisor:
    """Persist control intent, supervise the coordinator, and summarize status."""

    def __init__(
        self,
        *,
        project_root: str | Path,
        command: Sequence[str],
        queue_path: str | Path,
        status_path: str | Path,
        control_path: str | Path,
        workers: int,
        run_id: str | None = None,
        poll_seconds: float = 1.0,
        initial_backoff_seconds: float = 2.0,
        maximum_backoff_seconds: float = 300.0,
        stable_run_seconds: float = 60.0,
        spawn: SpawnCoordinator = _spawn_coordinator,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.command = tuple(str(value) for value in command)
        if not self.command or any(not value for value in self.command):
            raise ValueError("Coordinator command must contain non-empty argv entries.")
        if isinstance(workers, bool) or not 1 <= int(workers) <= 8:
            raise ValueError("workers must be between 1 and 8.")
        self.workers = int(workers)
        self.queue_path = Path(queue_path).resolve()
        self.status_path = Path(status_path).resolve()
        self.control = DashboardControlStore(control_path)
        persisted_workers = self.control.get_workers()
        if persisted_workers is not None:
            self.workers = persisted_workers
            self.command = self._command_with_workers(self.command, self.workers)
        self.run_id = run_id
        self.poll_seconds = _positive_seconds(poll_seconds, label="poll_seconds")
        self.initial_backoff_seconds = _positive_seconds(
            initial_backoff_seconds,
            label="initial_backoff_seconds",
        )
        self.maximum_backoff_seconds = _positive_seconds(
            maximum_backoff_seconds,
            label="maximum_backoff_seconds",
        )
        if self.maximum_backoff_seconds < self.initial_backoff_seconds:
            raise ValueError("maximum_backoff_seconds cannot be below the initial value.")
        self.stable_run_seconds = _positive_seconds(
            stable_run_seconds,
            label="stable_run_seconds",
        )
        self._spawn = spawn
        self._condition = threading.Condition(threading.RLock())
        self._process: CoordinatorProcess | None = None
        self._process_started_monotonic: float | None = None
        self._closed = False
        self._thread: threading.Thread | None = None
        self._failure_count = 0
        self._restart_count = 0
        self._next_launch_monotonic: float | None = None
        self._last_valid_status: dict[str, Any] = {}
        self._status_error_type: str | None = None
        self._events: deque[dict[str, str]] = deque(maxlen=40)

    @staticmethod
    def _command_with_workers(command: Sequence[str], workers: int) -> tuple[str, ...]:
        values = [str(value) for value in command]
        try:
            position = values.index("--workers")
        except ValueError:
            values.extend(("--workers", str(workers)))
        else:
            if position + 1 >= len(values):
                raise ValueError("Coordinator --workers argument lacks its value.")
            values[position + 1] = str(workers)
        return tuple(values)

    def _add_event(self, message: str) -> None:
        self._events.append({"at": _utc_now(), "message": message})

    def begin_supervision(self) -> None:
        with self._condition:
            if self._thread is not None and self._thread.is_alive():
                return
            self._closed = False
            self._thread = threading.Thread(
                target=self._supervise,
                name="model-dashboard-coordinator-supervisor",
                daemon=True,
            )
            self._thread.start()

    def _read_status(self) -> dict[str, Any]:
        if not self.status_path.is_file():
            return dict(self._last_valid_status)
        try:
            before = self.status_path.stat()
            payload = json.loads(self.status_path.read_text(encoding="utf-8"))
            after = self.status_path.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise RuntimeError("status changed during read")
            if not isinstance(payload, dict):
                raise ValueError("status must be an object")
        except (OSError, json.JSONDecodeError, RuntimeError, ValueError) as error:
            error_type = type(error).__name__
            if error_type != self._status_error_type:
                self._add_event(f"状态摘要暂不可读（{error_type}）；保留上一份有效摘要。")
            self._status_error_type = error_type
            return dict(self._last_valid_status)
        self._status_error_type = None
        self._last_valid_status = dict(payload)
        return payload

    def _effective_run_id(self, status: dict[str, Any]) -> str | None:
        candidate = self.run_id or status.get("run_id")
        if isinstance(candidate, str) and candidate.strip():
            return candidate
        return None

    def _set_queue_state(self, desired_state: str, status: dict[str, Any]) -> bool:
        run_id = self._effective_run_id(status)
        if run_id is None or not self.queue_path.is_file():
            return False
        try:
            ModelRunQueue(self.queue_path).set_desired_state(run_id, desired_state)
        except (OSError, RuntimeError, ValueError, RunNotFoundError):
            return False
        return True

    @staticmethod
    def _is_complete(status: dict[str, Any]) -> bool:
        # Fit completion is not analysis completion: OOF compilation/provenance
        # must finish before the supervisor may stop restarting the coordinator.
        return status.get("state") == "complete"

    @staticmethod
    def _is_intentional_terminal(status: dict[str, Any]) -> bool:
        """Do not restart a scientifically blocked run in a tight loop."""

        return status.get("state") in {"blocked", "complete"}

    def _record_exit(self, exit_code: int, *, ran_seconds: float) -> None:
        if ran_seconds >= self.stable_run_seconds:
            self._failure_count = 0
        self._failure_count += 1
        self._restart_count += 1
        delay = _backoff_seconds(
            self._failure_count,
            initial_seconds=self.initial_backoff_seconds,
            maximum_seconds=self.maximum_backoff_seconds,
        )
        self._next_launch_monotonic = time.monotonic() + delay
        outcome = "zero" if exit_code == 0 else "nonzero"
        self._add_event(
            f"协调器退出（{outcome}）；若仍处于运行意图，将在 {delay:g} 秒后重启。"
        )

    def _supervise(self) -> None:
        while True:
            with self._condition:
                if self._closed:
                    return
                status = self._read_status()
                desired = self.control.get_desired_state()
                self._set_queue_state(desired, status)
                process = self._process
                if process is not None:
                    exit_code = process.poll()
                    if exit_code is not None:
                        started = self._process_started_monotonic or time.monotonic()
                        ran_seconds = max(0.0, time.monotonic() - started)
                        self._process = None
                        self._process_started_monotonic = None
                        process = None
                        if (
                            desired == "running"
                            and not self._is_complete(status)
                            and not self._is_intentional_terminal(status)
                        ):
                            self._record_exit(exit_code, ran_seconds=ran_seconds)
                        else:
                            self._next_launch_monotonic = None
                if (
                    desired == "running"
                    and process is None
                    and not self._is_complete(status)
                    and not self._is_intentional_terminal(status)
                ):
                    now = time.monotonic()
                    if self._next_launch_monotonic is None or now >= self._next_launch_monotonic:
                        try:
                            self._process = self._spawn(self.command, self.project_root)
                        except Exception as error:  # noqa: BLE001 - status is type-only
                            self._failure_count += 1
                            self._restart_count += 1
                            delay = _backoff_seconds(
                                self._failure_count,
                                initial_seconds=self.initial_backoff_seconds,
                                maximum_seconds=self.maximum_backoff_seconds,
                            )
                            self._next_launch_monotonic = time.monotonic() + delay
                            self._add_event(
                                f"协调器启动失败（{type(error).__name__}）；"
                                f"{delay:g} 秒后自动重试。"
                            )
                        else:
                            self._process_started_monotonic = time.monotonic()
                            self._next_launch_monotonic = None
                            self._add_event("协调器已启动。")
                elif desired == "paused":
                    self._next_launch_monotonic = None
                self._condition.wait(timeout=self.poll_seconds)

    def start_or_resume(self) -> dict[str, Any]:
        assert_grouped_execution_authorized(self.project_root)
        self.control.set_desired_state("running")
        with self._condition:
            status = self._read_status()
            self._set_queue_state("running", status)
            self._next_launch_monotonic = None
            self._failure_count = 0
            self._add_event("收到开始或继续请求。")
            self._condition.notify_all()
        self.begin_supervision()
        return self.snapshot()

    def request_pause(self) -> dict[str, Any]:
        self.control.set_desired_state("paused")
        with self._condition:
            status = self._read_status()
            self._set_queue_state("paused", status)
            self._next_launch_monotonic = None
            self._add_event("收到安全暂停请求；不再领取新任务，等待当前任务完整落盘。")
            self._condition.notify_all()
        return self.snapshot()

    def set_workers(self, workers: int) -> dict[str, Any]:
        """Persist concurrency for the next Start; only legal while fully paused."""

        if isinstance(workers, bool) or not isinstance(workers, int) or not 1 <= workers <= 8:
            raise ValueError("workers must be an integer between 1 and 8.")
        with self._condition:
            current = self.snapshot()
            if current["state"] != "paused":
                raise WorkerChangeError(
                    "Worker count may be changed only after Safe Pause fully completes."
                )
            self.workers = workers
            self.command = self._command_with_workers(self.command, workers)
            self.control.set_workers(workers)
            self._add_event(f"Worker count set to {workers}; it applies on the next Start.")
            self._condition.notify_all()
        return self.snapshot()

    def _queue_summary(self, run_id: str | None) -> dict[str, Any]:
        if run_id is None or not self.queue_path.is_file():
            return {}
        try:
            queue = ModelRunQueue(self.queue_path)
            counts = queue.counts(run_id)
            counts_by_kind = queue.counts_by_kind(run_id)
            active = [
                {
                    "task_id": task.task_id,
                    "kind": task.kind,
                    "worker": task.lease_owner,
                    "elapsed_seconds": None,
                }
                for task in queue.list_tasks(run_id, statuses=("running",))
            ]
        except (OSError, RuntimeError, ValueError, RunNotFoundError):
            return {}
        return {
            "counts": counts,
            "counts_by_kind": counts_by_kind,
            "active_tasks": active,
        }

    @staticmethod
    def _clean_counts(value: Any) -> dict[str, int]:
        source = value if isinstance(value, dict) else {}
        result: dict[str, int] = {}
        for name in ("pending", "running", "complete", "quarantined", "total"):
            try:
                result[name] = max(0, int(source.get(name, 0)))
            except (TypeError, ValueError):
                result[name] = 0
        if result["total"] == 0:
            statuses = ("pending", "running", "complete", "quarantined")
            result["total"] = sum(result[name] for name in statuses)
        return result

    @staticmethod
    def _clean_active_tasks(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        result = []
        for raw in value:
            if not isinstance(raw, dict):
                continue
            elapsed = raw.get("elapsed_seconds")
            if not isinstance(elapsed, (int, float)) or not math.isfinite(float(elapsed)):
                elapsed = None
            result.append(
                {
                    "task_id": str(raw.get("task_id", "")),
                    "kind": str(raw.get("kind", "")),
                    "worker": str(raw.get("worker", raw.get("lease_owner", ""))),
                    "elapsed_seconds": elapsed,
                }
            )
        return result

    def snapshot(self) -> dict[str, Any]:
        with self._condition:
            status = self._read_status()
            desired = self.control.get_desired_state()
            run_id = self._effective_run_id(status)
            queue_summary = self._queue_summary(run_id)
            counts = self._clean_counts(queue_summary.get("counts", status.get("counts")))
            active_tasks = self._clean_active_tasks(
                status.get("active_tasks", queue_summary.get("active_tasks"))
            )
            active = max(counts["running"], len(active_tasks))
            process_running = self._process is not None and self._process.poll() is None
            if self._is_complete(status):
                state = "complete"
            elif status.get("state") == "blocked":
                state = "blocked"
            elif desired == "paused":
                state = "pausing" if active > 0 else "paused"
            elif process_running:
                reported = status.get("state")
                visible = {"running", "initializing", "compiling"}
                state = str(reported) if reported in visible else "running"
            elif self._next_launch_monotonic is not None:
                state = "restarting"
            else:
                state = "starting"
            eta = status.get("eta_seconds")
            if not isinstance(eta, (int, float)) or not math.isfinite(float(eta)):
                eta = None
            status_events = status.get("events")
            clean_events: list[dict[str, str]] = []
            if isinstance(status_events, list):
                for event in status_events[-20:]:
                    if isinstance(event, dict):
                        clean_events.append(
                            {
                                "at": str(event.get("at", "")),
                                "message": str(event.get("message", "")),
                            }
                        )
            error = status.get("error")
            clean_error = None
            if isinstance(error, dict) and error:
                clean_error = {
                    "type": str(error.get("error_type", error.get("type", "Unknown")))
                }
            return {
                "run_id": run_id,
                "desired_state": desired,
                "state": state,
                "counts": counts,
                "counts_by_kind": queue_summary.get(
                    "counts_by_kind", status.get("counts_by_kind", {})
                ),
                "total": counts["total"],
                "completed": counts["complete"],
                "pending": counts["pending"],
                "active": active,
                "quarantined": counts["quarantined"],
                "workers": self.workers,
                "eta_seconds": eta,
                "active_tasks": active_tasks,
                "events": (clean_events + list(self._events))[-20:],
                "error": clean_error,
                "coordinator_running": process_running,
                "coordinator_pid": self._process.pid if process_running else None,
                "automatic_restart_count": self._restart_count,
            }

    def shutdown(self) -> None:
        """Stop supervising without killing or corrupting an active coordinator."""

        with self._condition:
            self._closed = True
            self._condition.notify_all()
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(2.0, self.poll_seconds * 2))


class ModelDashboardRequestHandler(BaseHTTPRequestHandler):
    """Serve the local model dashboard and token-protected control API."""

    supervisor: ModelDashboardSupervisor
    control_token: str
    page: bytes

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def _send_json(
        self,
        payload: dict[str, Any],
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
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
            self._send_json(self.supervisor.snapshot())
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
        body = self.rfile.read(length) if length else b""
        if self.path == "/api/start":
            try:
                snapshot = self.supervisor.start_or_resume()
            except ExecutionTransferredOutError:
                self._send_json(
                    {"error": "execution_transferred_out"},
                    HTTPStatus.LOCKED,
                )
                return
            self._send_json(snapshot)
            return
        if self.path == "/api/pause":
            self._send_json(self.supervisor.request_pause())
            return
        if self.path == "/api/workers":
            try:
                payload = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._send_json({"error": "invalid_json"}, HTTPStatus.BAD_REQUEST)
                return
            if not isinstance(payload, dict) or set(payload) != {"workers"}:
                self._send_json({"error": "invalid_worker_payload"}, HTTPStatus.BAD_REQUEST)
                return
            try:
                snapshot = self.supervisor.set_workers(payload["workers"])
            except ValueError:
                self._send_json({"error": "invalid_workers"}, HTTPStatus.BAD_REQUEST)
                return
            except WorkerChangeError:
                self._send_json({"error": "pause_before_worker_change"}, HTTPStatus.CONFLICT)
                return
            self._send_json(snapshot)
            return
        self._send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)


class _DashboardHTTPServer(ThreadingHTTPServer):
    daemon_threads = True


def create_server(
    *,
    host: str,
    port: int,
    supervisor: ModelDashboardSupervisor,
    page_path: str | Path,
) -> ThreadingHTTPServer:
    """Create the local server and inject its random control token into the UI."""

    token = secrets.token_urlsafe(24)
    page = Path(page_path).read_text(encoding="utf-8")
    token_script = f"const __ISEF_CONTROL_TOKEN = {json.dumps(token)};\n"
    page = page.replace("<script>", f"<script>\n      {token_script}", 1)
    page = page.replace(
        'fetch(path, { method: "POST" })',
        'fetch(path, { method: "POST", headers: '
        '{"X-ISEF-Control": __ISEF_CONTROL_TOKEN} })',
    )
    handler = type(
        "BoundModelDashboardRequestHandler",
        (ModelDashboardRequestHandler,),
        {
            "supervisor": supervisor,
            "control_token": token,
            "page": page.encode("utf-8"),
        },
    )
    return _DashboardHTTPServer((host, port), handler)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--workers", type=int, choices=range(1, 9), default=4)
    parser.add_argument("--run-id")
    parser.add_argument("--run-directory", default=str(DEFAULT_RUN_DIRECTORY))
    parser.add_argument("--coordinator-script", default="scripts/run_grouped_models.py")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--initial-backoff-seconds", type=float, default=2.0)
    parser.add_argument("--maximum-backoff-seconds", type=float, default=300.0)
    arguments = parser.parse_args(argv)
    if arguments.host not in {"127.0.0.1", "localhost"}:
        raise ValueError("The model dashboard may bind only to localhost.")
    if not 1 <= arguments.port <= 65_535:
        raise ValueError("port must be between 1 and 65535.")

    project_root = Path(__file__).resolve().parents[2]
    run_directory = Path(arguments.run_directory)
    if not run_directory.is_absolute():
        run_directory = project_root / run_directory
    run_directory = run_directory.resolve()
    command = build_coordinator_command(
        project_root,
        workers=arguments.workers,
        coordinator_script=arguments.coordinator_script,
    )
    with DashboardProcessLock(run_directory / "dashboard.lock"):
        supervisor = ModelDashboardSupervisor(
            project_root=project_root,
            command=command,
            queue_path=run_directory / DEFAULT_QUEUE_FILENAME,
            status_path=run_directory / DEFAULT_STATUS_FILENAME,
            control_path=run_directory / DEFAULT_CONTROL_FILENAME,
            workers=arguments.workers,
            run_id=arguments.run_id,
            poll_seconds=arguments.poll_seconds,
            initial_backoff_seconds=arguments.initial_backoff_seconds,
            maximum_backoff_seconds=arguments.maximum_backoff_seconds,
        )
        page_path = project_root / "tools" / "model_dashboard" / "index.html"
        server = create_server(
            host=arguments.host,
            port=arguments.port,
            supervisor=supervisor,
            page_path=page_path,
        )
        url = f"http://{arguments.host}:{server.server_address[1]}/"
        print(f"Model dashboard ready at {url}", flush=True)
        supervisor.begin_supervision()
        if not arguments.no_browser:
            webbrowser.open(url)
        try:
            server.serve_forever(poll_interval=0.5)
        except KeyboardInterrupt:
            print("Safe pause requested; active fits will drain.", flush=True)
            supervisor.request_pause()
        finally:
            supervisor.shutdown()
            server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
