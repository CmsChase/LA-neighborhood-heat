"""Local progress UI for the single authorized three-city external claim."""

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
DEFAULT_PORT: Final = 8771
WORKER_CHOICES: Final = (1, 2, 3, 4)
DEFAULT_WORKERS: Final = 1
DATABASE_RELATIVE_PATH: Final = Path(
    "data/interim/multicity/targets/runtime/target_tasks.sqlite"
)
STATUS_RELATIVE_PATH: Final = Path(
    "data/interim/multicity/targets/runtime/external_worker_status.json"
)
CONTROL_RELATIVE_PATH: Final = Path(
    "data/interim/multicity/targets/runtime/external_dashboard_control.json"
)
LOG_RELATIVE_PATH: Final = Path(
    "data/interim/multicity/targets/runtime/external_worker.log"
)
AUTHORIZATION_RELATIVE_PATH: Final = Path(
    "manifests/multicity/targets/EXTERNAL_TARGET_AUTHORIZATION.json"
)
WORKER_RELATIVE_PATH: Final = Path("scripts/run_multicity_external_target_worker.py")
EXTERNAL_KINDS: Final = frozenset({"external_overpass", "external_compile"})
CITY_TOTALS: Final = {"phoenix_az": 23, "houston_tx": 22, "chicago_il": 22}
EXTERNAL_TOTAL: Final = 67


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
        raise ValueError("workers must be 1, 2, 3, or 4")
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
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def read(self) -> dict[str, Any]:
        with self._lock:
            payload = _read_json(self.path)
            desired = payload.get("desired_state", "paused")
            try:
                workers = _workers(payload.get("workers", DEFAULT_WORKERS))
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
    from la_heat.multicity.external_target_authorization import (
        authenticate_external_target_authorization,
    )

    return authenticate_external_target_authorization(
        root, root / AUTHORIZATION_RELATIVE_PATH
    )


def _default_initialize(root: Path) -> dict[str, Any]:
    from la_heat.multicity.target_runtime import initialize_target_runtime

    return initialize_target_runtime(root)


def _discover_run_id(root: Path) -> str | None:
    try:
        from la_heat.multicity.target_runtime import target_run_id
        from la_heat.multicity.target_transaction import stage_multicity_target_build_plan

        return target_run_id(stage_multicity_target_build_plan(root, check_only=True))
    except (OSError, RuntimeError, ValueError):
        return None


class ExternalTargetSupervisor:
    """Supervise one external-only worker; final_merge is never claimable here."""

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
        self._run_id = _discover_run_id(self.root)
        self._next_launch: float | None = None
        self._failures = 0
        self._restarts = 0
        self._events: deque[dict[str, str]] = deque(maxlen=40)

    def _event(self, message: str) -> None:
        self._events.append({"at": _utc_now(), "message": message})

    def _effective_run_id(self) -> str | None:
        if self._run_id:
            return self._run_id
        run_id = _read_json(self.status_path).get("run_id")
        return run_id if isinstance(run_id, str) and run_id else None

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
    def _empty_count(total: int) -> dict[str, int]:
        return {
            "pending": total,
            "running": 0,
            "complete": 0,
            "quarantined": 0,
            "total": total,
        }

    def _queue_snapshot(self) -> dict[str, Any]:
        run_id = self._effective_run_id()
        if not run_id or not self.queue_path.is_file():
            return {}
        try:
            queue = ModelRunQueue(self.queue_path)
            tasks = queue.list_tasks(run_id)
            desired = queue.get_desired_state(run_id)
        except (OSError, RuntimeError, ValueError, RunNotFoundError):
            return {}
        external = [task for task in tasks if task.kind in EXTERNAL_KINDS]
        cities = {city: self._empty_count(0) for city in CITY_TOTALS}
        active: list[str] = []
        current: list[dict[str, str]] = []
        for task in external:
            city = task.payload.get("city_id") if isinstance(task.payload, dict) else None
            if city not in cities:
                continue
            cities[city][task.status] += 1
            cities[city]["total"] += 1
            if task.status == "running":
                active.append(task.task_id)
                current.append(
                    {
                        "task_id": task.task_id,
                        "city_id": str(city),
                        "kind": task.kind,
                    }
                )
        total = {
            key: sum(city[key] for city in cities.values())
            for key in ("pending", "running", "complete", "quarantined", "total")
        }
        final = [task for task in tasks if task.kind == "final_merge"]
        final_sealed = bool(
            len(final) == 1
            and final[0].status == "pending"
            and final[0].attempt == 0
            and final[0].result is None
        )
        return {
            "run_id": run_id,
            "desired_state": desired,
            "total": total,
            "cities": cities,
            "active_task_ids": active,
            "current_tasks": current,
            "final_merge_sealed": final_sealed,
        }

    def _complete(self) -> bool:
        total = self._queue_snapshot().get("total", {})
        return total.get("total") == EXTERNAL_TOTAL and total.get("complete") == EXTERNAL_TOTAL

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
                        if desired == "running" and not self._complete():
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
                if desired == "running" and process is None and not self._complete():
                    if self._next_launch is None or time.monotonic() >= self._next_launch:
                        settings = self.control.read()
                        command = build_worker_command(self.root, workers=settings["workers"])
                        try:
                            self._process = self._spawn(command, self.root, self.log_path)
                        except Exception as error:  # noqa: BLE001 - expose type only
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
                            self._event(f"三城 external worker 已启动（{settings['workers']} workers）。")
                elif desired == "paused":
                    self._next_launch = None
                self._condition.wait(timeout=self._poll)

    def start_or_continue(self, *, workers: int) -> dict[str, Any]:
        workers = _workers(workers)
        self._authorize(self.root)  # Authentication only; no VALUES_OPENED creation.
        initialized = self._initialize(self.root)
        run_id = initialized.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            raise RuntimeError("target runtime did not return a run_id")
        self._run_id = run_id
        from la_heat.multicity.external_target_worker import validate_external_queue

        validate_external_queue(ModelRunQueue(self.queue_path), run_id)
        self.control.write(desired_state="running", workers=workers)
        if not self._set_queue_state("running"):
            raise RuntimeError("target queue could not enter running state")
        with self._condition:
            self._failures = 0
            self._next_launch = None
            self._event("完整三城授权已认证；开始或继续 external 67 个任务。")
            self._condition.notify_all()
        self.begin()
        return self.snapshot()

    def request_pause(self) -> dict[str, Any]:
        settings = self.control.read()
        self.control.write(desired_state="paused", workers=settings["workers"])
        self._set_queue_state("paused")
        with self._condition:
            self._next_launch = None
            self._event("已请求安全暂停：不领取新任务，当前任务落盘后退出。")
            self._condition.notify_all()
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        with self._condition:
            worker = _read_json(self.status_path)
            queue = self._queue_snapshot()
            settings = self.control.read()
            total = queue.get("total", self._empty_count(EXTERNAL_TOTAL))
            cities = queue.get(
                "cities", {city: self._empty_count(count) for city, count in CITY_TOTALS.items()}
            )
            process_running = self._process is not None and self._process.poll() is None
            desired = settings["desired_state"]
            final_sealed = queue.get("final_merge_sealed", True)
            if not final_sealed:
                state = "blocked_final_merge_changed"
            elif total["complete"] == EXTERNAL_TOTAL:
                state = "complete"
            elif desired == "paused":
                state = "pausing" if total["running"] else "paused"
            elif process_running:
                state = "running"
            elif self._next_launch is not None:
                state = "restarting"
            else:
                state = "starting"
            eta = worker.get("eta_seconds")
            if isinstance(eta, bool) or not isinstance(eta, int | float) or not math.isfinite(float(eta)):
                eta = None
            current = queue.get("current_tasks", [])
            return {
                "schema_version": 1,
                "state": state,
                "desired_state": desired,
                "run_id": queue.get("run_id", worker.get("run_id")),
                "workers": settings["workers"],
                "phase": worker.get("phase", "准备"),
                "total": total,
                "cities": cities,
                "current_tasks": current[:4] if isinstance(current, list) else [],
                "eta_seconds": eta,
                "retry_count": max(0, int(worker.get("retry_count", 0) or 0)),
                "automatic_restart_count": self._restarts,
                "worker_running": process_running,
                "restart_in_seconds": (
                    max(0.0, self._next_launch - time.monotonic())
                    if self._next_launch is not None
                    else None
                ),
                "last_error_type": worker.get("last_error_type"),
                "final_merge_sealed": final_sealed,
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


_PAGE = r'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>三城外部目标构建</title><style>
:root{color-scheme:dark;--bg:#090d10;--card:#141a1f;--line:#2a343c;--text:#f5f7f8;--muted:#8e9ba4;--cyan:#4ad7cf;--blue:#5ea3ff;--gold:#f0c45d;--red:#ff7474}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 85% 0,#163146,transparent 42%),var(--bg);color:var(--text);font:15px/1.5 system-ui,"Microsoft YaHei",sans-serif}main{max-width:1160px;margin:auto;padding:38px 24px}.top,.line,.controls{display:flex;align-items:center;justify-content:space-between;gap:15px}h1{font-size:34px;margin:0}.muted{color:var(--muted)}.badge{border:1px solid var(--line);border-radius:99px;padding:7px 14px}.controls{justify-content:flex-start;margin:26px 0}select,button{height:47px;border:1px solid var(--line);border-radius:10px;background:var(--card);color:var(--text);padding:0 18px;font:inherit}button{font-weight:800;cursor:pointer}#start{background:var(--blue);color:#07111b}#pause{background:#252d33}.bar{height:11px;background:#242c32;border-radius:99px;overflow:hidden}.fill{height:100%;width:0;background:linear-gradient(90deg,var(--blue),var(--cyan))}.cards{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin:18px 0}.card,.logs{background:var(--card);border:1px solid var(--line);border-radius:13px;padding:15px}.label{color:var(--muted);font-size:12px}.value{font-size:22px;font-weight:800;margin-top:3px}.sealed{color:var(--gold)}.current{margin:20px 2px}.log{height:270px;overflow:auto;background:#07090b;border-radius:8px;padding:12px;white-space:pre-wrap;font:12px/1.55 Consolas,monospace;color:#bbc7cf}.error{color:var(--red)}@media(max-width:800px){.cards{grid-template-columns:1fr 1fr}.top{align-items:flex-start;flex-direction:column}}
</style></head><body><main><div class="top"><div><div class="muted">ONE BLIND EXTERNAL CLAIM</div><h1>三城外部目标构建</h1><div class="muted">Phoenix · Houston · Chicago / 2025 · final_merge 保持封存</div></div><div id="state" class="badge">准备</div></div>
<div class="controls"><label>并行任务 <select id="workers"><option value="1">1（推荐）</option><option value="2">2</option><option value="3">3</option><option value="4">4</option></select></label><button id="start">开始 / 继续</button><button id="pause">安全暂停</button></div>
<div class="line"><strong id="count">总计 0 / 67</strong><span id="eta" class="muted">预计剩余：估算中</span></div><div class="bar"><div id="fill" class="fill"></div></div>
<div class="cards"><div class="card"><div class="label">阶段</div><div id="phase" class="value">准备</div></div><div class="card"><div class="label">Phoenix</div><div id="phoenix" class="value">0 / 23</div></div><div class="card"><div class="label">Houston</div><div id="houston" class="value">0 / 22</div></div><div class="card"><div class="label">Chicago</div><div id="chicago" class="value">0 / 22</div></div><div class="card"><div class="label">重试 / 重启</div><div id="retries" class="value">0 / 0</div></div></div>
<div class="current">当前任务：<b id="current">—</b> <span id="error" class="error"></span><br><span id="sealed" class="sealed">final_merge：SEALED</span></div><div class="logs"><b>最近日志</b><div id="log" class="log">等待开始……</div></div>
</main><script>const token="__TOKEN__",$=id=>document.getElementById(id);let first=true;function duration(v){if(v==null)return"估算中";v=Math.max(0,Math.round(v));let h=Math.floor(v/3600),m=Math.floor(v%3600/60),s=v%60;return h?`${h}小时 ${m}分钟`:m?`${m}分钟 ${s}秒`:`${s}秒`}function city(s,id,total){let c=s.cities?.[id]||{};return `${c.complete||0} / ${c.total||total}`}function render(s){let d=s.total?.complete||0,t=s.total?.total||67,p=t?100*d/t:0;$("state").textContent=s.state||"—";$("phase").textContent=s.phase||"—";$("count").textContent=`总计 ${d} / ${t} (${p.toFixed(1)}%)`;$("phoenix").textContent=city(s,"phoenix_az",23);$("houston").textContent=city(s,"houston_tx",22);$("chicago").textContent=city(s,"chicago_il",22);$("retries").textContent=`${s.retry_count||0} / ${s.automatic_restart_count||0}`;$("eta").textContent=`预计剩余：${duration(s.eta_seconds)}`;$("fill").style.width=`${Math.min(100,p)}%`;$("current").textContent=(s.current_tasks||[]).map(x=>`${x.city_id} · ${x.task_id}`).join(" | ")||"—";$("error").textContent=s.last_error_type?` · ${s.last_error_type}`:"";$("sealed").textContent=`final_merge：${s.final_merge_sealed?"SEALED":"CHANGED — STOP"}`;if(first){$("workers").value=String(s.workers||1);first=false}$("workers").disabled=!!s.worker_running;let lines=[...(s.events||[]).map(e=>`${e.at||""}  ${e.message||""}`),...(s.log_tail||[])];$("log").textContent=lines.slice(-100).join("\n")||"等待开始……";$("log").scrollTop=$("log").scrollHeight}async function act(path,body={}){let r=await fetch(path,{method:"POST",headers:{"Content-Type":"application/json","X-Target-Control":token},body:JSON.stringify(body)}),j=await r.json();if(!r.ok)throw Error(j.error||r.statusText);render(j)}async function refresh(){render(await(await fetch("/api/status",{cache:"no-store"})).json())}$("start").onclick=()=>act("/api/start",{workers:Number($("workers").value)}).catch(e=>alert(e.message));$("pause").onclick=()=>act("/api/pause").catch(e=>alert(e.message));refresh();setInterval(()=>refresh().catch(()=>{}),1000)</script></body></html>'''


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    supervisor: ExternalTargetSupervisor
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


def create_server(*, host: str, port: int, supervisor: ExternalTargetSupervisor) -> _Server:
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("external target dashboard must bind to localhost")
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
    supervisor = ExternalTargetSupervisor(args.project_root)
    server = create_server(host=args.host, port=args.port, supervisor=supervisor)
    url = f"http://{args.host}:{server.server_address[1]}/"
    print(f"Three-city external target dashboard: {url}", flush=True)
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

