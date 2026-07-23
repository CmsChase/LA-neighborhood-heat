"""Local dashboard for the frozen 2025 Sentinel predictor build.

The dashboard is deliberately an orchestration-only process.  It may launch
exactly one command:

``scripts/build_final_test_sentinel_features.py --workers {6,8}``

The feature builder owns all scientific state and publishes an atomic status
document.  Pause is cooperative and persistent: the dashboard creates
``PAUSE_REQUESTED`` and never kills a process selected by name or command-line
matching.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import re
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
from typing import Any, Protocol

TOTAL_ACQUISITIONS = 34
ALLOWED_WORKERS = frozenset({6, 8})
ENGINE_RELATIVE_PATH = Path("scripts/build_final_test_sentinel_features.py")
STATE_RELATIVE_DIRECTORY = Path("data/interim/final_test_2025/sentinel")
STATUS_FILENAME = "status.json"
PAUSE_FILENAME = "PAUSE_REQUESTED"
DASHBOARD_VERSION = "final-test-predictor-dashboard-v1"
DEFAULT_RESTART_DELAYS = (2.0, 5.0, 15.0, 30.0, 60.0)
STABLE_RUNTIME_SECONDS = 600.0
_SENSITIVE_URL_QUERY = re.compile(r"(https?://[^\s?]+)\?[^\s]+", re.IGNORECASE)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _safe_text(value: object, *, limit: int = 500) -> str:
    """Return bounded UI text without exposing signed URL query strings."""

    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    text = _SENSITIVE_URL_QUERY.sub(r"\1?<redacted>", text)
    return text[:limit]


def _nonnegative_count(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, (list, tuple, set, dict)):
        return len(value)
    return 0


def _current_items(value: object) -> list[str]:
    values = value if isinstance(value, list | tuple) else [value]
    current: list[str] = []
    for item in values:
        if item is None:
            continue
        if isinstance(item, dict):
            selected = next(
                (
                    item[key]
                    for key in (
                        "physical_acquisition_id",
                        "acquisition_id",
                        "id",
                        "item",
                    )
                    if key in item
                ),
                None,
            )
            if selected is not None:
                current.append(_safe_text(selected, limit=160))
        else:
            current.append(_safe_text(item, limit=160))
    return current[:8]


def _log_lines(value: object) -> list[str]:
    values = value if isinstance(value, list | tuple) else [value]
    lines: list[str] = []
    for item in values:
        if item is None:
            continue
        if isinstance(item, dict):
            timestamp = item.get("at") or item.get("time") or ""
            message = item.get("message") or item.get("event") or item.get("type")
            if message is not None:
                lines.append(_safe_text(f"{timestamp} {message}".strip()))
        else:
            lines.append(_safe_text(item))
    return lines[-30:]


def _finite_nonnegative_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    if number < 0 or number == float("inf") or number != number:
        return None
    return number


def empty_engine_status() -> dict[str, Any]:
    """Return the target-blind status shown before the engine has run."""

    return {
        "state": "not_started",
        "total": TOTAL_ACQUISITIONS,
        "completed": 0,
        "running": 0,
        "failed": 0,
        "current": [],
        "log_tail": [],
        "eta_seconds": None,
        "updated_at_utc": None,
        "status_contract_error": None,
    }


def normalize_engine_status(payload: object) -> dict[str, Any]:
    """Normalize the small, version-tolerant status contract used by the UI."""

    result = empty_engine_status()
    if not isinstance(payload, dict):
        result["status_contract_error"] = "status JSON is not an object"
        return result

    declared_total = payload.get("total", TOTAL_ACQUISITIONS)
    if declared_total != TOTAL_ACQUISITIONS:
        result["status_contract_error"] = (
            f"engine total must remain {TOTAL_ACQUISITIONS}"
        )

    state = payload.get("state")
    if isinstance(state, str) and state:
        result["state"] = _safe_text(state, limit=80)
    result["completed"] = min(
        TOTAL_ACQUISITIONS,
        _nonnegative_count(payload.get("completed", payload.get("completed_ids"))),
    )
    result["running"] = min(
        TOTAL_ACQUISITIONS,
        _nonnegative_count(payload.get("running", payload.get("active"))),
    )
    result["failed"] = min(
        TOTAL_ACQUISITIONS,
        _nonnegative_count(payload.get("failed", payload.get("failures"))),
    )
    result["current"] = _current_items(
        payload.get("current", payload.get("active", []))
    )
    result["log_tail"] = _log_lines(
        payload.get("log_tail", payload.get("events", payload.get("logs", [])))
    )
    result["eta_seconds"] = _finite_nonnegative_number(
        payload.get("eta_seconds", payload.get("estimated_remaining_seconds"))
    )
    updated = payload.get("updated_at_utc", payload.get("updated_at"))
    if isinstance(updated, str):
        result["updated_at_utc"] = _safe_text(updated, limit=80)
    return result


def read_engine_status(path: str | Path) -> dict[str, Any]:
    """Read one atomic engine status document without raising into the server."""

    status_path = Path(path)
    if not status_path.exists():
        return empty_engine_status()
    try:
        payload = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        status = empty_engine_status()
        status["status_contract_error"] = "status JSON could not be read"
        return status
    return normalize_engine_status(payload)


class DashboardAlreadyRunningError(RuntimeError):
    """A second dashboard attempted to supervise the same final build."""


class DashboardProcessLock:
    """OS-released single-instance lock for this dashboard."""

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
            raise DashboardAlreadyRunningError(
                "The final predictor dashboard is already running."
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


class ManagedProcess(Protocol):
    """Only the process surface required by the exact-child supervisor."""

    pid: int

    def poll(self) -> int | None:
        """Return the exit code, or ``None`` while the child is running."""


SpawnProcess = Callable[[tuple[str, ...], Path], ManagedProcess]


def build_engine_command(
    project_root: str | Path,
    *,
    workers: int,
) -> tuple[str, ...]:
    """Build the only command this dashboard is permitted to launch."""

    if workers not in ALLOWED_WORKERS:
        raise ValueError("Final Sentinel workers must be 6 or 8.")
    root = Path(project_root).resolve()
    engine_path = (root / ENGINE_RELATIVE_PATH).resolve()
    if engine_path.parent != (root / "scripts").resolve():
        raise RuntimeError("Final Sentinel engine path escaped the scripts directory.")
    return (
        sys.executable,
        str(engine_path),
        "--workers",
        str(workers),
    )


def _spawn_engine(command: tuple[str, ...], cwd: Path) -> ManagedProcess:
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    return subprocess.Popen(  # noqa: S603 - command is constructed above, no shell
        command,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creation_flags,
    )


def _write_pause_marker(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}")
    temporary.write_text(
        json.dumps(
            {
                "schema_version": DASHBOARD_VERSION,
                "requested_at_utc": _utc_now(),
                "intent": "pause",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


class FinalTestPredictorSupervisor:
    """Supervise exactly one final Sentinel feature-builder child."""

    def __init__(
        self,
        project_root: str | Path,
        *,
        workers: int = 6,
        spawn: SpawnProcess = _spawn_engine,
        restart_delays: Sequence[float] = DEFAULT_RESTART_DELAYS,
        poll_seconds: float = 0.5,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if workers not in ALLOWED_WORKERS:
            raise ValueError("Final Sentinel workers must be 6 or 8.")
        delays = tuple(float(delay) for delay in restart_delays)
        if not delays or any(delay <= 0 for delay in delays):
            raise ValueError("Restart delays must be positive and non-empty.")
        if poll_seconds <= 0:
            raise ValueError("Supervisor poll interval must be positive.")

        self.project_root = Path(project_root).resolve()
        self.state_directory = self.project_root / STATE_RELATIVE_DIRECTORY
        self.status_path = self.state_directory / STATUS_FILENAME
        self.pause_path = self.state_directory / PAUSE_FILENAME
        self._workers = workers
        self._spawn = spawn
        self._restart_delays = delays
        self._poll_seconds = poll_seconds
        self._monotonic = monotonic
        self._condition = threading.Condition(threading.RLock())
        self._child: ManagedProcess | None = None
        self._desired_running = False
        self._closed = False
        self._circuit_open = False
        self._consecutive_failures = 0
        self._automatic_restart_count = 0
        self._restart_at: float | None = None
        self._child_started_at: float | None = None
        self._completed_at_launch = 0
        self._events: deque[dict[str, str]] = deque(maxlen=60)
        self._monitor = threading.Thread(
            target=self._monitor_loop,
            name="final-test-predictor-supervisor",
            daemon=True,
        )
        self._monitor.start()

    def _event(self, message: str) -> None:
        self._events.append({"at": _utc_now(), "message": message})

    def _engine_status(self) -> dict[str, Any]:
        return read_engine_status(self.status_path)

    def _launch_locked(self) -> None:
        if (
            self._closed
            or not self._desired_running
            or self._circuit_open
            or self.pause_path.exists()
            or self._child is not None
        ):
            return
        command = build_engine_command(self.project_root, workers=self._workers)
        try:
            child = self._spawn(command, self.project_root)
        except Exception as exc:  # noqa: BLE001 - event is deliberately type-only
            self._event(f"Engine launch failed ({type(exc).__name__}).")
            self._record_unexpected_exit_locked()
            return
        self._child = child
        self._child_started_at = self._monotonic()
        self._completed_at_launch = self._engine_status()["completed"]
        self._restart_at = None
        self._event(f"Engine started with {self._workers} workers.")

    def _record_unexpected_exit_locked(self) -> None:
        status = self._engine_status()
        runtime = (
            0.0
            if self._child_started_at is None
            else max(0.0, self._monotonic() - self._child_started_at)
        )
        if (
            status["completed"] > self._completed_at_launch
            or runtime >= STABLE_RUNTIME_SECONDS
        ):
            self._consecutive_failures = 0
        self._consecutive_failures += 1
        self._child_started_at = None

        if self._consecutive_failures > len(self._restart_delays):
            self._circuit_open = True
            self._desired_running = False
            self._restart_at = None
            self._event("Automatic restart circuit opened.")
            return

        delay = self._restart_delays[self._consecutive_failures - 1]
        self._automatic_restart_count += 1
        self._restart_at = self._monotonic() + delay
        self._event(f"Engine restart scheduled in {delay:g} seconds.")

    def _handle_exit_locked(self, exit_code: int) -> None:
        self._child = None
        status = self._engine_status()
        if self.pause_path.exists() or not self._desired_running:
            self._restart_at = None
            self._event("Engine stopped at a pause boundary.")
            return
        if status["state"] == "complete" and status["completed"] == TOTAL_ACQUISITIONS:
            self._desired_running = False
            self._restart_at = None
            self._consecutive_failures = 0
            self._event("All Sentinel acquisitions are complete.")
            return
        self._event(f"Engine exited unexpectedly (code {int(exit_code)}).")
        self._record_unexpected_exit_locked()

    def _monitor_loop(self) -> None:
        while True:
            with self._condition:
                if self._closed:
                    return
                child = self._child
                if child is not None:
                    exit_code = child.poll()
                    if exit_code is not None:
                        self._handle_exit_locked(exit_code)
                        self._condition.notify_all()
                        continue
                elif (
                    self._desired_running
                    and not self._circuit_open
                    and not self.pause_path.exists()
                ):
                    if self._restart_at is None or (
                        self._monotonic() >= self._restart_at
                    ):
                        self._launch_locked()
                        self._condition.notify_all()
                        continue

                wait_seconds = self._poll_seconds
                if self._restart_at is not None:
                    wait_seconds = min(
                        wait_seconds,
                        max(0.01, self._restart_at - self._monotonic()),
                    )
                self._condition.wait(timeout=wait_seconds)

    def start_or_continue(self, workers: int) -> dict[str, Any]:
        """Clear a persisted pause and launch the exact engine command."""

        if workers not in ALLOWED_WORKERS:
            raise ValueError("Final Sentinel workers must be 6 or 8.")
        with self._condition:
            if self._closed:
                return self._snapshot_locked()
            if self._child is not None:
                return self._snapshot_locked()
            self.state_directory.mkdir(parents=True, exist_ok=True)
            self.pause_path.unlink(missing_ok=True)
            self._workers = workers
            self._desired_running = True
            self._circuit_open = False
            self._consecutive_failures = 0
            self._restart_at = None
            self._event("Start/continue requested.")
            self._launch_locked()
            self._condition.notify_all()
            return self._snapshot_locked()

    def request_pause(self) -> dict[str, Any]:
        """Persist pause intent; the owned child exits cooperatively."""

        with self._condition:
            _write_pause_marker(self.pause_path)
            self._desired_running = False
            self._restart_at = None
            self._circuit_open = False
            self._event("Pause requested; active acquisitions may finish safely.")
            self._condition.notify_all()
            return self._snapshot_locked()

    def _snapshot_locked(self) -> dict[str, Any]:
        engine = self._engine_status()
        child_alive = self._child is not None and self._child.poll() is None
        pause_requested = self.pause_path.exists()
        state = engine["state"]
        if self._circuit_open:
            state = "restart_circuit_open"
        elif pause_requested and child_alive:
            state = "pausing"
        elif pause_requested:
            state = "paused"
        elif child_alive:
            state = "running"
        elif self._desired_running and self._restart_at is not None:
            state = "restarting"
        elif engine["completed"] == TOTAL_ACQUISITIONS:
            state = "complete"

        restart_in = None
        if self._restart_at is not None:
            restart_in = max(0.0, self._restart_at - self._monotonic())
        return {
            **engine,
            "state": state,
            "progress_fraction": engine["completed"] / TOTAL_ACQUISITIONS,
            "pause_requested": pause_requested,
            "workers": self._workers,
            "managed_pid": None if self._child is None else self._child.pid,
            "engine_process_running": child_alive,
            "desired_running": self._desired_running,
            "automatic_restart_count": self._automatic_restart_count,
            "consecutive_process_failures": self._consecutive_failures,
            "restart_in_seconds": restart_in,
            "restart_circuit_open": self._circuit_open,
            "supervisor_log_tail": list(self._events)[-20:],
            "dashboard_version": DASHBOARD_VERSION,
        }

    def snapshot(self) -> dict[str, Any]:
        with self._condition:
            return self._snapshot_locked()

    def close(self, *, request_pause: bool = False) -> None:
        """Stop the dashboard monitor without process-name based termination."""

        if request_pause:
            self.request_pause()
        with self._condition:
            self._closed = True
            self._condition.notify_all()
        if self._monitor is not threading.current_thread():
            self._monitor.join(timeout=5)


_PAGE = """<!doctype html>
<html lang="zh-CN">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>2025 Sentinel 特征任务</title>
<style>
body{font:16px system-ui;margin:24px auto;max-width:900px;padding:0 16px;background:#111;color:#eee}
h1{font-size:22px}.bar{height:18px;background:#333;border-radius:9px;overflow:hidden}
#fill{height:100%;background:#2878df;width:0}
.cards{display:flex;gap:10px;flex-wrap:wrap;margin:14px 0}
.card{background:#202020;padding:12px;min-width:130px;border-radius:8px}.n{font-size:25px}
button,select{font:inherit;padding:8px 13px;margin-right:8px}button{cursor:pointer}
pre{background:#1b1b1b;padding:12px;white-space:pre-wrap;max-height:280px;overflow:auto}
.warn{color:#ffd761}
</style>
<h1>2025 Sentinel‑2 特征任务</h1>
<p>状态：<b id="state">读取中</b>　预计剩余：<span id="eta">—</span></p>
<div class="bar"><div id="fill"></div></div>
<div class="cards">
 <div class="card">完成<div class="n"><span id="done">0</span> / 34</div></div>
 <div class="card">运行<div class="n" id="running">0</div></div>
 <div class="card">失败<div class="n" id="failed">0</div></div>
 <div class="card">自动重启<div class="n" id="restarts">0</div></div>
</div>
<p>
 <select id="workers">
  <option value="6">6 workers</option><option value="8">8 workers</option>
 </select>
 <button id="start">Start / Continue</button><button id="pause">Pause</button>
</p>
<p>当前项：<span id="current">—</span></p><p class="warn" id="warning"></p>
<h2>日志尾</h2><pre id="logs">—</pre>
<script>
const token="__CONTROL_TOKEN__";
const $=id=>document.getElementById(id);
function duration(s){if(s==null)return "估算中";s=Math.max(0,Math.round(s));
 const h=Math.floor(s/3600),m=Math.floor((s%3600)/60);return h?`${h} 小时 ${m} 分`:`${m} 分钟`;}
function render(x){$("state").textContent=x.state;$("done").textContent=x.completed;
 $("running").textContent=x.running;$("failed").textContent=x.failed;
 $("restarts").textContent=x.automatic_restart_count;$("eta").textContent=duration(x.eta_seconds);
 $("fill").style.width=(100*x.progress_fraction).toFixed(1)+"%";
 $("current").textContent=(x.current||[]).join(", ")||"—";
 $("warning").textContent=x.status_contract_error||
  (x.restart_circuit_open?"自动重启已熔断，请检查日志后再次点击 Start / Continue。":"");
 const a=(x.log_tail||[]),b=(x.supervisor_log_tail||[]).map(v=>`${v.at} ${v.message}`);
 $("logs").textContent=a.concat(b).slice(-30).join("\\n")||"—";
 $("workers").value=String(x.workers);$("workers").disabled=x.engine_process_running;
}
async function refresh(){try{
 render(await (await fetch("/api/status",{cache:"no-store"})).json());
}catch(e){}}
async function action(path,body={}){const r=await fetch(path,{method:"POST",
 headers:{"Content-Type":"application/json","X-Final-Test-Control":token},body:JSON.stringify(body)});
 render(await r.json());}
$("start").onclick=()=>action("/api/start",{workers:Number($("workers").value)});
$("pause").onclick=()=>action("/api/pause");refresh();setInterval(refresh,1000);
</script>
</html>
"""


class _DashboardServer(ThreadingHTTPServer):
    supervisor: FinalTestPredictorSupervisor
    control_token: str


class _Handler(BaseHTTPRequestHandler):
    server: _DashboardServer

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path == "/":
            page = _PAGE.replace("__CONTROL_TOKEN__", self.server.control_token)
            data = page.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return
        if self.path == "/api/status":
            self._send_json(self.server.supervisor.snapshot(), HTTPStatus.OK)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        supplied = self.headers.get("X-Final-Test-Control", "")
        if not hmac.compare_digest(supplied, self.server.control_token):
            self._send_json({"error": "forbidden"}, HTTPStatus.FORBIDDEN)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json({"error": "invalid request"}, HTTPStatus.BAD_REQUEST)
            return
        if length > 4096:
            self._send_json({"error": "request too large"}, HTTPStatus.BAD_REQUEST)
            return
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send_json({"error": "invalid JSON"}, HTTPStatus.BAD_REQUEST)
            return

        if self.path == "/api/start":
            try:
                workers = body.get("workers") if isinstance(body, dict) else None
                payload = self.server.supervisor.start_or_continue(workers)
            except ValueError as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return
            self._send_json(payload, HTTPStatus.OK)
            return
        if self.path == "/api/pause":
            self._send_json(
                self.server.supervisor.request_pause(),
                HTTPStatus.OK,
            )
            return
        self.send_error(HTTPStatus.NOT_FOUND)


def create_server(
    *,
    host: str,
    port: int,
    supervisor: FinalTestPredictorSupervisor,
) -> _DashboardServer:
    """Create a loopback-only HTTP server; it never starts the engine."""

    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Final predictor dashboard must bind to localhost.")
    if not 0 <= port <= 65_535:
        raise ValueError("Dashboard port must be between 0 and 65535.")
    server = _DashboardServer((host, port), _Handler)
    server.supervisor = supervisor
    server.control_token = secrets.token_urlsafe(32)
    return server


def main(argv: Sequence[str] | None = None) -> int:
    """Run the dashboard without automatically starting computation."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, choices=(6, 8), default=6)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--no-browser", action="store_true")
    arguments = parser.parse_args(argv)

    project_root = Path(__file__).resolve().parents[2]
    state_directory = project_root / STATE_RELATIVE_DIRECTORY
    lock_path = state_directory / "dashboard.lock"
    try:
        with DashboardProcessLock(lock_path):
            supervisor = FinalTestPredictorSupervisor(
                project_root,
                workers=arguments.workers,
            )
            try:
                server = create_server(
                    host=arguments.host,
                    port=arguments.port,
                    supervisor=supervisor,
                )
                url = f"http://{arguments.host}:{server.server_address[1]}/"
                print(f"Final predictor dashboard: {url}")
                if not arguments.no_browser:
                    webbrowser.open(url)
                try:
                    server.serve_forever()
                except KeyboardInterrupt:
                    pass
                finally:
                    server.server_close()
            finally:
                supervisor.close(request_pause=True)
    except DashboardAlreadyRunningError as exc:
        print(str(exc))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
