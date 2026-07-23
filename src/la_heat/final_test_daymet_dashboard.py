"""Minimal localhost controller for the six frozen 2025 Daymet subsets.

Earthdata credentials are accepted only through the loopback control request,
copied into the exact child process environment, and immediately discarded by
the dashboard.  They are never written to a file, command line, status object,
or log.  The dashboard suppresses child output for the same reason.
"""

from __future__ import annotations

import argparse
import csv
import hmac
import json
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
from typing import Any, Protocol

TOTAL_SUBSETS = 6
ENGINE_RELATIVE_PATH = Path("scripts/stage_final_test_daymet_grid.py")
CONFIG_RELATIVE_PATH = Path("configs/research.toml")
MANIFEST_RELATIVE_DIRECTORY = Path("manifests/final_test_2025/daymet_grid")
PROVENANCE_FILENAME = "DAYMET_GRID.json"
DOWNLOADS_FILENAME = "subset_downloads.csv"
LOCK_RELATIVE_PATH = Path(
    "data/interim/final_test_2025/daymet/dashboard.lock"
)
EARTHDATA_ENVIRONMENT_VARIABLE = "EARTHDATA_TOKEN"
EARTHDATA_ENVIRONMENT_VARIABLES = (
    "EARTHDATA_TOKEN",
    "NASA_EARTHDATA_TOKEN",
    "EDL_TOKEN",
)
DEFAULT_TERMINATE_GRACE_SECONDS = 5.0


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def build_engine_command(project_root: str | Path) -> tuple[str, ...]:
    """Return the sole command that this dashboard may launch."""

    root = Path(project_root).resolve()
    engine = (root / ENGINE_RELATIVE_PATH).resolve()
    if engine.parent != (root / "scripts").resolve():
        raise RuntimeError("Daymet engine path escaped the scripts directory.")
    return (
        sys.executable,
        str(engine),
        "--config",
        CONFIG_RELATIVE_PATH.as_posix(),
        "--download-subsets",
    )


def _count_download_rows(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames is None or not {"variable", "year"}.issubset(
                reader.fieldnames
            ):
                return 0
            keys = {
                (str(row.get("variable", "")), str(row.get("year", "")))
                for row in reader
                if row.get("variable") and row.get("year")
            }
    except (OSError, UnicodeError, csv.Error):
        return 0
    return min(TOTAL_SUBSETS, len(keys))


def read_download_progress(project_root: str | Path) -> dict[str, Any]:
    """Read only non-secret atomic manifest fields for the progress display."""

    root = Path(project_root).resolve()
    directory = root / MANIFEST_RELATIVE_DIRECTORY
    completed = _count_download_rows(directory / DOWNLOADS_FILENAME)
    state = "paused"
    status_error: str | None = None
    provenance = directory / PROVENANCE_FILENAME
    if provenance.exists():
        try:
            payload = json.loads(provenance.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            status_error = "Daymet provenance could not be read."
        else:
            if not isinstance(payload, dict):
                status_error = "Daymet provenance is not an object."
            else:
                declared_total = payload.get("expected_subset_count")
                if declared_total not in (None, TOTAL_SUBSETS):
                    status_error = "Daymet subset total is not the frozen value 6."
                value = payload.get("completed_subset_count", 0)
                if isinstance(value, int) and not isinstance(value, bool):
                    completed = max(completed, min(TOTAL_SUBSETS, max(0, value)))
                if payload.get("state") == "subsets_complete" and completed == 6:
                    state = "complete"
    if completed == TOTAL_SUBSETS:
        state = "complete"
    return {
        "state": state,
        "completed": completed,
        "total": TOTAL_SUBSETS,
        "progress_fraction": completed / TOTAL_SUBSETS,
        "status_error": status_error,
    }


class DaymetDashboardAlreadyRunningError(RuntimeError):
    """A second Daymet dashboard attempted to acquire the same lock."""


class DaymetDashboardLock:
    """OS-released, single-instance lock for the Daymet controller."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._stream: Any = None

    def __enter__(self) -> DaymetDashboardLock:
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
            raise DaymetDashboardAlreadyRunningError(
                "The final Daymet dashboard is already running."
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
    """Exact child-process surface; no process-name discovery is used."""

    pid: int

    def poll(self) -> int | None:
        """Return an exit code or ``None`` while running."""

    def terminate(self) -> None:
        """Request termination of this exact child."""

    def kill(self) -> None:
        """Force termination of this exact child."""


SpawnProcess = Callable[
    [tuple[str, ...], Path, Mapping[str, str]],
    ManagedProcess,
]


def _spawn_engine(
    command: tuple[str, ...],
    cwd: Path,
    environment: Mapping[str, str],
) -> ManagedProcess:
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    return subprocess.Popen(  # noqa: S603 - exact local script, never a shell
        command,
        cwd=cwd,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creation_flags,
    )


def _validated_token(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("An Earthdata bearer token is required.")
    if value != value.strip() or "\0" in value or "\r" in value or "\n" in value:
        raise ValueError("The Earthdata bearer token has invalid whitespace.")
    return value


class FinalTestDaymetSupervisor:
    """Start, observe, and pause only the exact Daymet child it owns."""

    def __init__(
        self,
        project_root: str | Path,
        *,
        spawn: SpawnProcess = _spawn_engine,
        poll_seconds: float = 0.25,
        terminate_grace_seconds: float = DEFAULT_TERMINATE_GRACE_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if poll_seconds <= 0 or terminate_grace_seconds <= 0:
            raise ValueError("Polling and termination grace periods must be positive.")
        self.project_root = Path(project_root).resolve()
        self._spawn = spawn
        self._poll_seconds = poll_seconds
        self._terminate_grace_seconds = terminate_grace_seconds
        self._monotonic = monotonic
        self._condition = threading.Condition(threading.RLock())
        self._child: ManagedProcess | None = None
        self._pause_requested = True
        self._terminate_deadline: float | None = None
        self._closed = False
        self._session_state = "paused"
        self._events: deque[dict[str, str]] = deque(maxlen=30)
        self._monitor = threading.Thread(
            target=self._monitor_loop,
            name="final-test-daymet-supervisor",
            daemon=True,
        )
        self._monitor.start()

    def _event(self, message: str) -> None:
        self._events.append({"at": _utc_now(), "message": message})

    def _handle_exit_locked(self, exit_code: int) -> None:
        self._child = None
        self._terminate_deadline = None
        progress = read_download_progress(self.project_root)
        if progress["completed"] == TOTAL_SUBSETS:
            self._session_state = "complete"
            self._event("All six Daymet subsets are complete.")
        elif self._pause_requested:
            self._session_state = "paused"
            self._event("Daymet download stopped; atomic files remain resumable.")
        elif exit_code == 0:
            self._session_state = "paused"
            self._event("Daymet process exited before all subsets were complete.")
        else:
            self._session_state = "error"
            self._event(f"Daymet process exited (code {int(exit_code)}).")

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
                    if (
                        self._terminate_deadline is not None
                        and self._monotonic() >= self._terminate_deadline
                    ):
                        child.kill()
                        self._terminate_deadline = None
                        self._event("Daymet child required forced termination.")
                self._condition.wait(timeout=self._poll_seconds)

    def start_or_resume(self, earthdata_token: object) -> dict[str, Any]:
        """Launch once with a short-lived, environment-only credential copy."""

        token = _validated_token(earthdata_token)
        with self._condition:
            if self._closed:
                return self._snapshot_locked()
            if self._child is not None and self._child.poll() is None:
                return self._snapshot_locked()
            if read_download_progress(self.project_root)["completed"] == TOTAL_SUBSETS:
                self._session_state = "complete"
                return self._snapshot_locked()

            command = build_engine_command(self.project_root)
            environment = dict(os.environ)
            for name in EARTHDATA_ENVIRONMENT_VARIABLES:
                environment.pop(name, None)
            environment[EARTHDATA_ENVIRONMENT_VARIABLE] = token
            try:
                child = self._spawn(
                    command,
                    self.project_root,
                    environment,
                )
            except Exception as exc:  # noqa: BLE001 - type only; token never logged
                self._session_state = "error"
                self._event(f"Daymet launch failed ({type(exc).__name__}).")
            else:
                self._child = child
                self._pause_requested = False
                self._terminate_deadline = None
                self._session_state = "running"
                self._event("Daymet download started or resumed.")
            finally:
                environment[EARTHDATA_ENVIRONMENT_VARIABLE] = ""
                environment.clear()
                token = ""
            self._condition.notify_all()
            return self._snapshot_locked()

    def request_pause(self) -> dict[str, Any]:
        """Terminate only the owned child; atomic output makes resume safe."""

        with self._condition:
            self._pause_requested = True
            child = self._child
            if child is None or child.poll() is not None:
                self._session_state = "paused"
                self._terminate_deadline = None
            else:
                self._session_state = "pausing"
                child.terminate()
                self._terminate_deadline = (
                    self._monotonic() + self._terminate_grace_seconds
                )
                self._event("Pause requested for the owned Daymet child.")
            self._condition.notify_all()
            return self._snapshot_locked()

    def _snapshot_locked(self) -> dict[str, Any]:
        progress = read_download_progress(self.project_root)
        child_alive = self._child is not None and self._child.poll() is None
        state = self._session_state
        if progress["completed"] == TOTAL_SUBSETS:
            state = "complete"
        elif child_alive and self._pause_requested:
            state = "pausing"
        elif child_alive:
            state = "running"
        return {
            **progress,
            "state": state,
            "engine_process_running": child_alive,
            "managed_pid": None if self._child is None else self._child.pid,
            "log_tail": list(self._events)[-20:],
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
        if self._monitor is not threading.current_thread():
            self._monitor.join(timeout=5)


_PAGE = """<!doctype html>
<html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>2025 Daymet 下载</title>
<style>
body{font:16px system-ui;max-width:760px;margin:28px auto;padding:0 16px;background:#111;color:#eee}
.bar{height:18px;background:#333;border-radius:9px;overflow:hidden}
#fill{height:100%;width:0;background:#2878df}
input,button{font:inherit;padding:9px;margin:12px 6px 12px 0}
input{width:min(440px,90%)}pre{background:#1d1d1d;padding:12px;white-space:pre-wrap}
</style>
<h1>2025 Daymet 下载</h1>
<p>状态：<b id="state">读取中</b>　进度：<b><span id="done">0</span> / 6</b></p>
<div class="bar"><div id="fill"></div></div>
<p><input id="token" type="password" autocomplete="off"
 placeholder="Earthdata bearer token（仅保存在内存）"></p>
<p><button id="start">Start / Resume</button><button id="pause">Pause</button></p>
<p id="warning"></p><h2>日志尾</h2><pre id="logs">—</pre>
<script>
const control="__CONTROL_TOKEN__";const $=id=>document.getElementById(id);
function render(x){$("state").textContent=x.state;$("done").textContent=x.completed;
 $("fill").style.width=(100*x.progress_fraction).toFixed(1)+"%";
 $("warning").textContent=x.status_error||"";
 $("logs").textContent=(x.log_tail||[]).map(v=>`${v.at} ${v.message}`).join("\\n")||"—";}
async function refresh(){try{
 render(await (await fetch("/api/status",{cache:"no-store"})).json());
}catch(e){}}
async function post(path,body){const r=await fetch(path,{method:"POST",
 headers:{"Content-Type":"application/json","X-Daymet-Control":control},
 body:JSON.stringify(body)});render(await r.json());}
$("start").onclick=async()=>{let value=$("token").value;$("token").value="";
 try{await post("/api/start",{earthdata_token:value});}finally{value="";}};
$("pause").onclick=()=>post("/api/pause",{});refresh();setInterval(refresh,1000);
</script></html>
"""


class _DaymetServer(ThreadingHTTPServer):
    supervisor: FinalTestDaymetSupervisor
    control_token: str


class _Handler(BaseHTTPRequestHandler):
    server: _DaymetServer

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _json(self, payload: dict[str, Any], status: HTTPStatus) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path == "/":
            data = _PAGE.replace(
                "__CONTROL_TOKEN__",
                self.server.control_token,
            ).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return
        if self.path == "/api/status":
            self._json(self.server.supervisor.snapshot(), HTTPStatus.OK)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        supplied = self.headers.get("X-Daymet-Control", "")
        if not hmac.compare_digest(supplied, self.server.control_token):
            self._json({"error": "forbidden"}, HTTPStatus.FORBIDDEN)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json({"error": "invalid request"}, HTTPStatus.BAD_REQUEST)
            return
        if length > 16_384:
            self._json({"error": "request too large"}, HTTPStatus.BAD_REQUEST)
            return
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._json({"error": "invalid JSON"}, HTTPStatus.BAD_REQUEST)
            return

        if self.path == "/api/start":
            token = body.get("earthdata_token") if isinstance(body, dict) else None
            try:
                payload = self.server.supervisor.start_or_resume(token)
            except ValueError as exc:
                self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return
            finally:
                token = None
                body = None
            self._json(payload, HTTPStatus.OK)
            return
        if self.path == "/api/pause":
            self._json(self.server.supervisor.request_pause(), HTTPStatus.OK)
            return
        self.send_error(HTTPStatus.NOT_FOUND)


def create_server(
    *,
    host: str,
    port: int,
    supervisor: FinalTestDaymetSupervisor,
) -> _DaymetServer:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Final Daymet dashboard must bind to localhost.")
    if not 0 <= port <= 65_535:
        raise ValueError("Dashboard port must be between 0 and 65535.")
    server = _DaymetServer((host, port), _Handler)
    server.supervisor = supervisor
    server.control_token = secrets.token_urlsafe(32)
    return server


def main(argv: Sequence[str] | None = None) -> int:
    """Run a paused-by-default Daymet dashboard on localhost."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--no-browser", action="store_true")
    arguments = parser.parse_args(argv)

    project_root = Path(__file__).resolve().parents[2]
    lock_path = project_root / LOCK_RELATIVE_PATH
    try:
        with DaymetDashboardLock(lock_path):
            supervisor = FinalTestDaymetSupervisor(project_root)
            try:
                server = create_server(
                    host=arguments.host,
                    port=arguments.port,
                    supervisor=supervisor,
                )
                url = f"http://{arguments.host}:{server.server_address[1]}/"
                print(f"Final Daymet dashboard: {url}")
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
    except DaymetDashboardAlreadyRunningError as exc:
        print(str(exc))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
