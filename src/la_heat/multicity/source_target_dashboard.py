"""Local UI and resilient supervisor for the authorized LA source-target build.

The dashboard never opens a raster or creates ``VALUES_OPENED``.  Start only
authenticates the existing source-lane permit, initializes the frozen queue,
sets its desired state, and launches the separate scientific worker.
"""

# ruff: noqa: E501

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

from la_heat.model_run_queue import ModelRunQueue, RunNotFoundError

DEFAULT_HOST: Final = "127.0.0.1"
DEFAULT_PORT: Final = 8770
WORKER_CHOICES: Final = (1, 2)
DEFAULT_WORKERS: Final = 1
DATABASE_RELATIVE_PATH: Final = Path(
    "data/interim/multicity/targets/runtime/target_tasks.sqlite"
)
STATUS_RELATIVE_PATH: Final = Path(
    "data/interim/multicity/targets/runtime/source_worker_status.json"
)
CONTROL_RELATIVE_PATH: Final = Path(
    "data/interim/multicity/targets/runtime/source_dashboard_control.json"
)
LOG_RELATIVE_PATH: Final = Path(
    "data/interim/multicity/targets/runtime/source_worker.log"
)
AUTHORIZATION_RELATIVE_PATH: Final = Path(
    "manifests/multicity/targets/SOURCE_TARGET_AUTHORIZATION.json"
)
WORKER_RELATIVE_PATH: Final = Path("scripts/run_multicity_source_target_worker.py")
SOURCE_KINDS: Final = frozenset({"source_overpass", "source_compile"})
EXTERNAL_KINDS: Final = frozenset(
    {"external_overpass", "external_compile", "final_merge"}
)
SOURCE_TOTAL: Final = 91
EXTERNAL_SEALED_TOTAL: Final = 68


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


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _tail(path: Path, maximum_bytes: int = 64_000) -> list[str]:
    if not path.is_file():
        return []
    try:
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - maximum_bytes))
            return stream.read().decode("utf-8", errors="replace").splitlines()[-80:]
    except OSError:
        return []


def _workers(value: object) -> int:
    if isinstance(value, bool) or value not in WORKER_CHOICES:
        raise ValueError("workers must be 1 or 2")
    return int(value)


def build_worker_command(project_root: str | Path, *, workers: int) -> tuple[str, ...]:
    root = Path(project_root).resolve()
    return (
        sys.executable,
        str((root / WORKER_RELATIVE_PATH).resolve()),
        "--project-root",
        str(root),
        "--workers",
        str(_workers(workers)),
        "--start",
    )


class DashboardControlStore:
    """Persist only desired state and the next worker count."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def read(self) -> dict[str, Any]:
        with self._lock:
            payload = _read_json(self.path)
            desired = payload.get("desired_state", "paused")
            workers = payload.get("workers", DEFAULT_WORKERS)
            try:
                workers = _workers(workers)
            except ValueError:
                workers = DEFAULT_WORKERS
            return {
                "desired_state": desired if desired in {"running", "paused"} else "paused",
                "workers": workers,
            }

    def write(self, *, desired_state: str, workers: int) -> None:
        if desired_state not in {"running", "paused"}:
            raise ValueError("desired_state must be running or paused")
        with self._lock:
            _atomic_json(
                {
                    "schema_version": 1,
                    "desired_state": desired_state,
                    "workers": _workers(workers),
                    "updated_at_utc": _utc_now(),
                },
                self.path,
            )


class ManagedProcess(Protocol):
    pid: int

    def poll(self) -> int | None: ...


SpawnProcess = Callable[[tuple[str, ...], Path, Path], ManagedProcess]
Authorize = Callable[[Path], object]
Initialize = Callable[[Path], dict[str, Any]]


class _LoggedProcess:
    def __init__(self, process: subprocess.Popen[bytes], stream: Any) -> None:
        self.process = process
        self.stream = stream
        self.pid = process.pid

    def poll(self) -> int | None:
        code = self.process.poll()
        if code is not None and not self.stream.closed:
            self.stream.close()
        return code


def _spawn(command: tuple[str, ...], cwd: Path, log_path: Path) -> ManagedProcess:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    stream = log_path.open("ab", buffering=0)
    try:
        process = subprocess.Popen(  # noqa: S603 - fixed local script, no shell
            command,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except Exception:
        stream.close()
        raise
    return _LoggedProcess(process, stream)


def _default_authorize(root: Path) -> object:
    from la_heat.multicity.source_target_authorization import (
        authenticate_source_target_authorization,
    )

    return authenticate_source_target_authorization(
        root, root / AUTHORIZATION_RELATIVE_PATH
    )


def _default_initialize(root: Path) -> dict[str, Any]:
    from la_heat.multicity.target_runtime import initialize_target_runtime

    return initialize_target_runtime(root)


class SourceTargetSupervisor:
    """Supervise one resumable source-lane worker without opening target values."""

    def __init__(
        self,
        project_root: str | Path,
        *,
        spawn: SpawnProcess = _spawn,
        authorize: Authorize = _default_authorize,
        initialize: Initialize = _default_initialize,
        poll_seconds: float = 0.5,
        initial_backoff_seconds: float = 2.0,
        maximum_backoff_seconds: float = 120.0,
    ) -> None:
        self.root = Path(project_root).resolve()
        self.queue_path = self.root / DATABASE_RELATIVE_PATH
        self.status_path = self.root / STATUS_RELATIVE_PATH
        self.log_path = self.root / LOG_RELATIVE_PATH
        self.control = DashboardControlStore(self.root / CONTROL_RELATIVE_PATH)
        self._spawn = spawn
        self._authorize = authorize
        self._initialize = initialize
        self._poll = max(0.01, float(poll_seconds))
        self._initial_backoff = max(0.01, float(initial_backoff_seconds))
        self._maximum_backoff = max(self._initial_backoff, float(maximum_backoff_seconds))
        self._condition = threading.Condition(threading.RLock())
        self._process: ManagedProcess | None = None
        self._thread: threading.Thread | None = None
        self._closed = False
        self._run_id: str | None = None
        self._next_launch: float | None = None
        self._failures = 0
        self._restarts = 0
        self._events: deque[dict[str, str]] = deque(maxlen=40)

    def _event(self, message: str) -> None:
        self._events.append({"at": _utc_now(), "message": message})

    def _status(self) -> dict[str, Any]:
        return _read_json(self.status_path)

    def _effective_run_id(self) -> str | None:
        status_run_id = self._status().get("run_id")
        if self._run_id:
            return self._run_id
        return status_run_id if isinstance(status_run_id, str) and status_run_id else None

    def _set_queue_state(self, state: str) -> bool:
        run_id = self._effective_run_id()
        if not run_id or not self.queue_path.is_file():
            return False
        try:
            ModelRunQueue(self.queue_path).set_desired_state(run_id, state)
        except (OSError, RuntimeError, ValueError, RunNotFoundError):
            return False
        return True

    @staticmethod
    def _kind_summary(value: Mapping[str, int] | None) -> dict[str, int]:
        source = value or {}
        return {
            key: max(0, int(source.get(key, 0)))
            for key in ("pending", "running", "complete", "quarantined", "total")
        }

    def _queue_snapshot(self) -> dict[str, Any]:
        run_id = self._effective_run_id()
        if not run_id or not self.queue_path.is_file():
            return {}
        try:
            queue = ModelRunQueue(self.queue_path)
            by_kind = queue.counts_by_kind(run_id)
            active = [
                task.task_id
                for task in queue.list_tasks(run_id, statuses=("running",))
                if task.kind in SOURCE_KINDS
            ]
            desired = queue.get_desired_state(run_id)
        except (OSError, RuntimeError, ValueError, RunNotFoundError):
            return {}

        def combined(kinds: frozenset[str], fallback_total: int) -> dict[str, int]:
            rows = [self._kind_summary(by_kind.get(kind)) for kind in kinds]
            result = {
                key: sum(row[key] for row in rows)
                for key in ("pending", "running", "complete", "quarantined", "total")
            }
            if result["total"] == 0:
                result["pending"] = fallback_total
                result["total"] = fallback_total
            return result

        return {
            "run_id": run_id,
            "desired_state": desired,
            "source": combined(SOURCE_KINDS, SOURCE_TOTAL),
            "external": combined(EXTERNAL_KINDS, EXTERNAL_SEALED_TOTAL),
            "active_task_ids": active,
        }

    def begin(self) -> None:
        with self._condition:
            if self._thread is not None and self._thread.is_alive():
                return
            self._closed = False
            self._thread = threading.Thread(target=self._supervise, daemon=True)
            self._thread.start()

    def _supervise(self) -> None:
        while True:
            with self._condition:
                if self._closed:
                    return
                desired = self.control.read()["desired_state"]
                self._set_queue_state(desired)
                process = self._process
                if process is not None:
                    code = process.poll()
                    if code is not None:
                        self._process = None
                        process = None
                        if desired == "running" and not self._source_complete():
                            self._failures += 1
                            self._restarts += 1
                            delay = min(
                                self._initial_backoff * (2 ** min(self._failures - 1, 10)),
                                self._maximum_backoff,
                            )
                            self._next_launch = time.monotonic() + delay
                            self._event(f"worker 退出（code {code}），{delay:g} 秒后自动重启。")
                        else:
                            self._next_launch = None
                if desired == "running" and process is None and not self._source_complete():
                    if self._next_launch is None or time.monotonic() >= self._next_launch:
                        settings = self.control.read()
                        command = build_worker_command(self.root, workers=settings["workers"])
                        try:
                            self._process = self._spawn(command, self.root, self.log_path)
                        except Exception as error:  # noqa: BLE001 - log only type
                            self._failures += 1
                            self._restarts += 1
                            delay = min(
                                self._initial_backoff * (2 ** min(self._failures - 1, 10)),
                                self._maximum_backoff,
                            )
                            self._next_launch = time.monotonic() + delay
                            self._event(f"worker 启动失败（{type(error).__name__}），自动重试。")
                        else:
                            self._next_launch = None
                            self._event(f"LA source worker 已启动（{settings['workers']} workers）。")
                elif desired == "paused":
                    self._next_launch = None
                self._condition.wait(timeout=self._poll)

    def _source_complete(self) -> bool:
        source = self._queue_snapshot().get("source", {})
        return source.get("total") == SOURCE_TOTAL and source.get("complete") == SOURCE_TOTAL

    def start_or_continue(self, *, workers: int) -> dict[str, Any]:
        workers = _workers(workers)
        self._authorize(self.root)  # Authentication only; never creates VALUES_OPENED.
        initialized = self._initialize(self.root)
        run_id = initialized.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            raise RuntimeError("target runtime did not return a run_id")
        self._run_id = run_id
        self.control.write(desired_state="running", workers=workers)
        if not self._set_queue_state("running"):
            raise RuntimeError("target queue could not enter running state")
        with self._condition:
            self._failures = 0
            self._next_launch = None
            self._event("已认证 LA source 许可；开始或继续。")
            self._condition.notify_all()
        self.begin()
        return self.snapshot()

    def request_pause(self) -> dict[str, Any]:
        settings = self.control.read()
        self.control.write(desired_state="paused", workers=settings["workers"])
        self._set_queue_state("paused")
        with self._condition:
            self._next_launch = None
            self._event("已请求安全暂停：不领取新任务，当前任务完成后退出。")
            self._condition.notify_all()
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        with self._condition:
            worker_status = self._status()
            queue = self._queue_snapshot()
            settings = self.control.read()
            source = queue.get(
                "source",
                {"pending": SOURCE_TOTAL, "running": 0, "complete": 0, "quarantined": 0, "total": SOURCE_TOTAL},
            )
            external = queue.get(
                "external",
                {"pending": EXTERNAL_SEALED_TOTAL, "running": 0, "complete": 0, "quarantined": 0, "total": EXTERNAL_SEALED_TOTAL},
            )
            process_running = self._process is not None and self._process.poll() is None
            desired = settings["desired_state"]
            if source["complete"] == SOURCE_TOTAL:
                state = "complete"
            elif desired == "paused":
                state = "pausing" if source["running"] else "paused"
            elif process_running:
                state = "running"
            elif self._next_launch is not None:
                state = "restarting"
            else:
                state = "starting"
            eta = worker_status.get("eta_seconds")
            if isinstance(eta, bool) or not isinstance(eta, int | float) or not math.isfinite(float(eta)):
                eta = None
            current = worker_status.get("active_task_ids", queue.get("active_task_ids", []))
            if not isinstance(current, list):
                current = []
            error_type = worker_status.get("last_error_type")
            return {
                "schema_version": 1,
                "state": state,
                "desired_state": desired,
                "run_id": queue.get("run_id", worker_status.get("run_id")),
                "workers": settings["workers"],
                "phase": worker_status.get("phase", "准备"),
                "source": source,
                "external": external,
                "external_sealed": True,
                "current_task_ids": [str(value) for value in current[:4]],
                "eta_seconds": eta,
                "retry_count": max(0, int(worker_status.get("retry_count", 0) or 0)),
                "automatic_restart_count": self._restarts,
                "worker_running": process_running,
                "restart_in_seconds": (
                    max(0.0, self._next_launch - time.monotonic())
                    if self._next_launch is not None
                    else None
                ),
                "last_error_type": str(error_type) if error_type else None,
                "events": list(self._events),
                "log_tail": _tail(self.log_path),
                "target_values_opened_by_dashboard": False,
            }

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(2.0, self._poll * 2))


_PAGE = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>LA 训练目标构建</title><style>
:root{color-scheme:dark;--bg:#0b0e11;--card:#151a1f;--line:#2a333b;--text:#f4f7f9;--muted:#93a1aa;--blue:#58a6ff;--gold:#f2c14e;--red:#ff6b6b}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 75% 0,#172433,transparent 38%),var(--bg);font:15px/1.5 system-ui,"Microsoft YaHei",sans-serif;color:var(--text)}main{max-width:1100px;margin:auto;padding:42px 24px}.top,.line,.controls{display:flex;align-items:center;justify-content:space-between;gap:16px}h1{margin:0;font-size:34px}.muted{color:var(--muted)}.badge{border:1px solid var(--line);border-radius:99px;padding:7px 14px}.controls{justify-content:flex-start;margin:28px 0}select,button{height:48px;border:1px solid var(--line);border-radius:11px;background:var(--card);color:var(--text);padding:0 20px;font:inherit}button{font-weight:800;cursor:pointer}#start{background:var(--blue);color:#07111b}#pause{background:#252c32}.bar{height:11px;background:#252c32;border-radius:99px;overflow:hidden}.fill{height:100%;width:0;background:linear-gradient(90deg,#58a6ff,#65d9c5)}.cards{display:grid;grid-template-columns:repeat(5,1fr);gap:13px;margin:19px 0}.card,.logs{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px}.label{color:var(--muted);font-size:12px}.value{font-size:23px;font-weight:800;margin-top:3px}.sealed{color:var(--gold)}.current{margin:20px 2px}.log{height:280px;overflow:auto;background:#080a0c;border-radius:9px;padding:13px;white-space:pre-wrap;font:12px/1.55 Consolas,monospace;color:#bac5cc}.error{color:var(--red)}@media(max-width:760px){.cards{grid-template-columns:1fr 1fr}.top{align-items:flex-start;flex-direction:column}}
</style></head><body><main><div class="top"><div><div class="muted">AUTHORIZED SOURCE LANE</div><h1>Los Angeles 训练目标构建</h1><div class="muted">2020–2024 Landsat LST · 外部三城保持封存</div></div><div id="state" class="badge">准备</div></div>
<div class="controls"><label>并行任务 <select id="workers"><option value="1">1（推荐）</option><option value="2">2</option></select></label><button id="start">开始 / 继续</button><button id="pause">安全暂停</button></div>
<div class="line"><strong id="count">LA 0 / 91</strong><span id="eta" class="muted">预计剩余：估算中</span></div><div class="bar"><div id="fill" class="fill"></div></div>
<div class="cards"><div class="card"><div class="label">阶段</div><div id="phase" class="value">准备</div></div><div class="card"><div class="label">LA 已完成</div><div id="done" class="value">0 / 91</div></div><div class="card"><div class="label">正在运行</div><div id="running" class="value">0</div></div><div class="card"><div class="label">重试 / 重启</div><div id="retries" class="value">0 / 0</div></div><div class="card"><div class="label">外部测试组</div><div id="external" class="value sealed">SEALED 0 / 68</div></div></div>
<div class="current">当前任务：<b id="current">—</b> <span id="error" class="error"></span></div><div class="logs"><b>最近日志</b><div id="log" class="log">等待开始…</div></div>
</main><script>const token="__TOKEN__",$=id=>document.getElementById(id);let first=true;function esc(v){return String(v??"")}function duration(v){if(v==null)return"估算中";v=Math.max(0,Math.round(v));let h=Math.floor(v/3600),m=Math.floor(v%3600/60),s=v%60;return h?`${h}小时 ${m}分钟`:m?`${m}分钟 ${s}秒`:`${s}秒`}function render(s){let d=s.source?.complete||0,t=s.source?.total||91,p=t?100*d/t:0;$("state").textContent=s.state||"—";$("phase").textContent=s.phase||"—";$("count").textContent=`LA ${d} / ${t} (${p.toFixed(1)}%)`;$("done").textContent=`${d} / ${t}`;$("running").textContent=s.source?.running||0;$("external").textContent=`SEALED ${s.external?.complete||0} / ${s.external?.total||68}`;$("retries").textContent=`${s.retry_count||0} / ${s.automatic_restart_count||0}`;$("eta").textContent=`预计剩余：${duration(s.eta_seconds)}`;$("fill").style.width=`${Math.min(100,p)}%`;$("current").textContent=(s.current_task_ids||[]).join(" · ")||"—";$("error").textContent=s.last_error_type?` · ${s.last_error_type}`:"";if(first){$("workers").value=String(s.workers||1);first=false}$("workers").disabled=!!s.worker_running;let lines=[...(s.events||[]).map(e=>`${e.at||""}  ${e.message||""}`),...(s.log_tail||[])];$("log").textContent=lines.slice(-100).join("\\n")||"等待开始…";$("log").scrollTop=$("log").scrollHeight}async function act(path,body={}){let r=await fetch(path,{method:"POST",headers:{"Content-Type":"application/json","X-Target-Control":token},body:JSON.stringify(body)}),j=await r.json();if(!r.ok)throw Error(j.error||r.statusText);render(j)}async function refresh(){render(await(await fetch("/api/status",{cache:"no-store"})).json())}$("start").onclick=()=>act("/api/start",{workers:Number($("workers").value)}).catch(e=>alert(e.message));$("pause").onclick=()=>act("/api/pause").catch(e=>alert(e.message));refresh();setInterval(()=>refresh().catch(()=>{}),1000)</script></body></html>"""


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    supervisor: SourceTargetSupervisor
    token: str


class _Handler(BaseHTTPRequestHandler):
    server: _Server

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _json(self, payload: Mapping[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path in {"/", "/index.html"}:
            body = _PAGE.replace("__TOKEN__", self.server.token).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/status":
            self._json(self.server.supervisor.snapshot())
        else:
            self._json({"error": "not_found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        if self.headers.get("X-Target-Control") != self.server.token:
            self._json({"error": "forbidden"}, HTTPStatus.FORBIDDEN)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, UnicodeError, json.JSONDecodeError):
            self._json({"error": "invalid_json"}, HTTPStatus.BAD_REQUEST)
            return
        if not isinstance(payload, dict):
            self._json({"error": "invalid_payload"}, HTTPStatus.BAD_REQUEST)
            return
        if self.path == "/api/start":
            try:
                result = self.server.supervisor.start_or_continue(
                    workers=payload.get("workers", DEFAULT_WORKERS)
                )
            except (OSError, RuntimeError, ValueError) as error:
                self._json({"error": str(error)}, HTTPStatus.CONFLICT)
                return
            self._json(result)
        elif self.path == "/api/pause":
            self._json(self.server.supervisor.request_pause())
        else:
            self._json({"error": "not_found"}, HTTPStatus.NOT_FOUND)


def create_server(*, host: str, port: int, supervisor: SourceTargetSupervisor) -> _Server:
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("source target dashboard must bind to localhost")
    server = _Server((host, port), _Handler)
    server.supervisor = supervisor
    server.token = secrets.token_urlsafe(24)
    return server


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=str(Path(__file__).resolve().parents[3]))
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--auto-start", action="store_true")
    parser.add_argument("--workers", type=int, choices=WORKER_CHOICES, default=DEFAULT_WORKERS)
    args = parser.parse_args(argv)
    supervisor = SourceTargetSupervisor(args.project_root)
    server = create_server(host=args.host, port=args.port, supervisor=supervisor)
    url = f"http://{args.host}:{server.server_address[1]}/"
    print(f"LA source target dashboard: {url}", flush=True)
    supervisor.begin()
    if args.auto_start:
        supervisor.start_or_continue(workers=args.workers)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        supervisor.request_pause()
    finally:
        supervisor.close()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
