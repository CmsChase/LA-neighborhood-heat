# ruff: noqa: E501
"""Tiny local progress and pause/resume UI for the remaining long research jobs."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Final

from la_heat.provenance import atomic_json

DEFAULT_HOST: Final = "127.0.0.1"
DEFAULT_PORT: Final = 8765
STRICT_TOTAL: Final = 90
CONTROL_FILENAME: Final = ".research_runner_control.json"
STRICT_OUTPUT_RELATIVE: Final = Path("data/interim/targets_sensitivity_stqa2")
ABLATION_STATUS_RELATIVE: Final = Path("data/interim/feature_ablation/status.json")
ABLATION_COMPILE_ROOT_RELATIVE: Final = Path("data/processed/feature_ablation")
STRICT_CONFIG_RELATIVE: Final = Path("configs/research_stqa2_sensitivity.toml")


class RunnerUiError(RuntimeError):
    """Raised when a requested runner control operation cannot be completed."""


@dataclass(frozen=True)
class RunnerPaths:
    """Fixed local paths used by the controller."""

    root: Path
    strict_output: Path
    ablation_status: Path
    ablation_compile_root: Path
    strict_config: Path
    control: Path
    stdout_log: Path
    stderr_log: Path

    @classmethod
    def from_root(cls, root: str | Path) -> RunnerPaths:
        project = Path(root).resolve()
        strict = project / STRICT_OUTPUT_RELATIVE
        return cls(
            root=project,
            strict_output=strict,
            ablation_status=project / ABLATION_STATUS_RELATIVE,
            ablation_compile_root=project / ABLATION_COMPILE_ROOT_RELATIVE,
            strict_config=project / STRICT_CONFIG_RELATIVE,
            control=strict / CONTROL_FILENAME,
            stdout_log=project
            / "data/interim/targets_sensitivity_stqa2.ui.stdout.log",
            stderr_log=project
            / "data/interim/targets_sensitivity_stqa2.ui.stderr.log",
        )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def read_control(paths: RunnerPaths) -> dict[str, Any]:
    """Read the persistent desired state; running is the safe default."""

    try:
        payload = json.loads(paths.control.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"desired_state": "running", "updated_at_utc": None}
    except (OSError, json.JSONDecodeError):
        return {"desired_state": "paused", "updated_at_utc": None}
    if not isinstance(payload, dict) or payload.get("desired_state") not in {
        "running",
        "paused",
    }:
        return {"desired_state": "paused", "updated_at_utc": None}
    return payload


def write_control(paths: RunnerPaths, desired_state: str) -> dict[str, Any]:
    """Persist a validated pause/run intent atomically."""

    if desired_state not in {"running", "paused"}:
        raise ValueError("desired_state must be running or paused")
    payload = {
        "schema_version": 1,
        "desired_state": desired_state,
        "updated_at_utc": _utc_now(),
    }
    paths.strict_output.mkdir(parents=True, exist_ok=True)
    atomic_json(payload, paths.control)
    return payload


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _strict_summaries(paths: RunnerPaths) -> list[Path]:
    directory = paths.strict_output / "by_overpass"
    if not directory.is_dir():
        return []
    return sorted(
        (path for path in directory.glob("*/summary.json") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
    )


def estimate_remaining_seconds(summary_paths: Sequence[Path], total: int) -> float | None:
    """Estimate remaining time from the robust median of recent cache completions."""

    if len(summary_paths) < 3 or len(summary_paths) >= total:
        return 0.0 if len(summary_paths) >= total else None
    recent = list(summary_paths[-16:])
    times = [path.stat().st_mtime for path in recent]
    intervals = [
        right - left
        for left, right in zip(times, times[1:], strict=False)
        if 5.0 <= right - left <= 20.0 * 60.0
    ]
    if not intervals:
        return None
    return float(statistics.median(intervals) * (total - len(summary_paths)))


def _format_eta(seconds: float | None) -> str:
    if seconds is None:
        return "估算中"
    if seconds <= 0:
        return "已完成"
    minutes = max(1, round(seconds / 60.0))
    if minutes < 60:
        return f"约 {minutes} 分钟"
    hours = minutes / 60.0
    return f"约 {hours:.1f} 小时"


def _latest_target_event(paths: RunnerPaths) -> tuple[str | None, str | None]:
    candidates = [
        paths.stdout_log,
        paths.strict_output.with_suffix(".supervisor.stdout.log"),
        paths.strict_output.with_suffix(".retry1.stdout.log"),
    ]
    existing = [path for path in candidates if path.is_file()]
    if not existing:
        return None, None
    newest = max(existing, key=lambda path: path.stat().st_mtime)
    try:
        lines = newest.read_text(encoding="utf-8", errors="replace").splitlines()[-80:]
    except OSError:
        return None, None
    events = [line.strip() for line in lines if line.strip().startswith("[target]")]
    return (events[-1] if events else None), datetime.fromtimestamp(
        newest.stat().st_mtime, tz=UTC
    ).isoformat()


_DISCOVERY_SCRIPT: Final = r"""
$items = @(Get-CimInstance Win32_Process | Where-Object {
  $_.Name -eq 'python.exe' -and
  $_.CommandLine -like '*targets_sensitivity_stqa2*'
} | Select-Object ProcessId, ParentProcessId, CommandLine)
$items | ConvertTo-Json -Compress
"""


def discover_strict_processes() -> list[dict[str, Any]]:
    """Return only Python processes whose command line owns the strict output tree."""

    if os.name != "nt":
        return []
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", _DISCOVERY_SCRIPT],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=15,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return []
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    rows = payload if isinstance(payload, list) else [payload]
    return [
        row
        for row in rows
        if isinstance(row, dict) and isinstance(row.get("ProcessId"), int)
    ]


def stop_strict_processes(processes: Sequence[dict[str, Any]]) -> None:
    """Stop the exact cache-safe strict process family captured by discovery."""

    process_ids = sorted(
        {
            int(row["ProcessId"])
            for row in processes
            if isinstance(row.get("ProcessId"), int)
        },
        reverse=True,
    )
    if not process_ids:
        return
    if os.name != "nt":
        raise RunnerUiError("Pause control is currently implemented for Windows only.")
    joined = ",".join(str(value) for value in process_ids)
    script = f"Stop-Process -Id @({joined}) -Force -ErrorAction SilentlyContinue"
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", script],
        capture_output=True,
        check=False,
        timeout=15,
    )


def launch_strict_supervisor(paths: RunnerPaths) -> int:
    """Launch the existing lock-protected resumable wrapper without a console window."""

    python = paths.root / ".venv/Scripts/python.exe"
    script = paths.root / "scripts/run_target_build_resumable.py"
    if not python.is_file() or not script.is_file() or not paths.strict_config.is_file():
        raise RunnerUiError("The venv, resumable wrapper, or strict config is missing.")
    paths.stdout_log.parent.mkdir(parents=True, exist_ok=True)
    command = [
        str(python),
        str(script),
        "--config",
        str(paths.strict_config),
        "--output-directory",
        str(paths.strict_output),
        "--max-attempts",
        "100",
        "--retry-delay-seconds",
        "30",
    ]
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    with paths.stdout_log.open("a", encoding="utf-8") as stdout, paths.stderr_log.open(
        "a", encoding="utf-8"
    ) as stderr:
        child = subprocess.Popen(  # noqa: S603
            command,
            cwd=paths.root,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            close_fds=True,
            creationflags=creationflags,
        )
    return int(child.pid)


class RunnerController:
    """Thread-safe controller used by both HTTP requests and the watchdog."""

    def __init__(
        self,
        paths: RunnerPaths,
        *,
        discover: Callable[[], list[dict[str, Any]]] = discover_strict_processes,
        stop: Callable[[Sequence[dict[str, Any]]], None] = stop_strict_processes,
        launch: Callable[[RunnerPaths], int] = launch_strict_supervisor,
    ) -> None:
        self.paths = paths
        self._discover = discover
        self._stop = stop
        self._launch = launch
        self._lock = threading.Lock()
        self._last_action: str | None = None
        self._last_error: str | None = None

    def _complete(self) -> bool:
        progress = _read_json(self.paths.strict_output / "build_progress.json")
        return progress.get("build_complete") is True and progress.get("state") in {
            "model_ready",
            "complete_gate_failed",
        }

    def ensure_running(self) -> None:
        """Auto-restart only when persisted intent is running and work is incomplete."""

        with self._lock:
            if read_control(self.paths)["desired_state"] != "running" or self._complete():
                return
            if self._discover():
                return
            try:
                pid = self._launch(self.paths)
                self._last_action = f"自动启动 PID {pid}"
                self._last_error = None
            except (OSError, RunnerUiError) as error:
                self._last_error = type(error).__name__

    def pause(self) -> None:
        """Persist pause first, then stop only the discovered strict process family."""

        with self._lock:
            write_control(self.paths, "paused")
            processes = self._discover()
            self._stop(processes)
            self._last_action = f"已暂停；停止 {len(processes)} 个相关 Python 进程"
            self._last_error = None

    def resume(self) -> None:
        """Persist running intent and launch only when no owner already exists."""

        write_control(self.paths, "running")
        self._last_action = "已请求继续"
        self.ensure_running()

    def status(self) -> dict[str, Any]:
        summaries = _strict_summaries(self.paths)
        completed = len(summaries)
        progress = _read_json(self.paths.strict_output / "build_progress.json")
        control = read_control(self.paths)
        processes = self._discover()
        event, event_time = _latest_target_event(self.paths)
        ablation = _read_json(self.paths.ablation_status)
        compile_files = list(
            self.paths.ablation_compile_root.glob("*/feature_ablation_compile_provenance.json")
        )
        strict_complete = self._complete()
        return {
            "schema_version": 1,
            "updated_at": datetime.now().astimezone().isoformat(),
            "strict": {
                "completed": completed,
                "total": STRICT_TOTAL,
                "percent": round(100.0 * completed / STRICT_TOTAL, 1),
                "remaining": STRICT_TOTAL - completed,
                "desired_state": control["desired_state"],
                "running": bool(processes),
                "process_ids": [int(row["ProcessId"]) for row in processes],
                "complete": strict_complete,
                "build_state": progress.get("state"),
                "eta": _format_eta(estimate_remaining_seconds(summaries, STRICT_TOTAL)),
                "latest_event": event,
                "latest_event_at": event_time,
            },
            "ablation": {
                "completed": int(ablation.get("completed", 0)),
                "total": int(ablation.get("total", 0)),
                "active": int(ablation.get("active", 0)),
                "quarantined": int(ablation.get("quarantined", 0)),
                "state": ablation.get("state", "unknown"),
                "compile_complete": bool(compile_files),
            },
            "controller": {
                "last_action": self._last_action,
                "last_error": self._last_error,
                "cache_safe_pause": True,
                "automatic_restart": True,
            },
        }


HTML: Final = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ISEF 后台任务</title>
<style>
body{font:16px system-ui;margin:0;background:#111;color:#eee}main{max-width:780px;margin:30px auto;padding:18px}
.card{background:#1d1d1d;border:1px solid #383838;border-radius:12px;padding:18px;margin:14px 0}
.row{display:flex;justify-content:space-between;gap:12px;align-items:center}.big{font-size:28px;font-weight:700}
.bar{height:14px;background:#3a3a3a;border-radius:8px;overflow:hidden;margin:12px 0}.fill{height:100%;background:#3182f6}
button{font-size:16px;padding:10px 18px;margin:8px 8px 0 0;border:0;border-radius:8px;cursor:pointer}
.pause{background:#ffcc33}.resume{background:#2ecc71}.muted{color:#aaa;font-size:14px}.ok{color:#68dc8b}
</style></head><body><main><h1>ISEF 后台任务</h1><div id="root">读取中…</div></main>
<script>
const esc=s=>String(s??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function action(name){await fetch('/api/'+name,{method:'POST'});await refresh()}
async function refresh(){try{const s=await (await fetch('/api/status')).json(),t=s.strict,a=s.ablation;
document.querySelector('#root').innerHTML=`
<section class="card"><div class="row"><b>严格 ST_QA≤2 K 重建</b><span>${esc(t.running?'运行中':t.desired_state==='paused'?'已暂停':'正在重启')}</span></div>
<div class="big">${t.completed} / ${t.total}（${t.percent}%）</div><div class="bar"><div class="fill" style="width:${t.percent}%"></div></div>
<div>预计剩余：${esc(t.eta)}　剩余日期：${t.remaining}</div><p class="muted">${esc(t.latest_event)}</p>
<button class="pause" onclick="action('pause')">暂停</button><button class="resume" onclick="action('resume')">继续</button></section>
<section class="card"><div class="row"><b>特征消融</b><span class="ok">${esc(a.state)}</span></div>
<div class="big">${a.completed} / ${a.total}</div><div>隔离任务：${a.quarantined}　编译：${a.compile_complete?'完成':'等待'}</div></section>
<p class="muted">自动重启已开启；暂停会保留全部已完成缓存。页面每 3 秒刷新。<br>更新时间：${esc(s.updated_at)} ${esc(s.controller.last_action||'')}</p>`
}catch(e){document.querySelector('#root').textContent='读取失败：'+e}}refresh();setInterval(refresh,3000);
</script></body></html>"""


def make_handler(controller: RunnerController) -> type[BaseHTTPRequestHandler]:
    """Bind one controller into a small HTTP request handler."""

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path in {"/", "/index.html"}:
                self._send(HTTPStatus.OK, HTML.encode(), "text/html; charset=utf-8")
                return
            if self.path == "/api/status":
                controller.ensure_running()
                body = json.dumps(controller.status(), ensure_ascii=False).encode()
                self._send(HTTPStatus.OK, body, "application/json; charset=utf-8")
                return
            self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")

        def do_POST(self) -> None:  # noqa: N802
            try:
                if self.path == "/api/pause":
                    controller.pause()
                elif self.path == "/api/resume":
                    controller.resume()
                else:
                    self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
                    return
            except (OSError, RunnerUiError) as error:
                body = json.dumps({"error": type(error).__name__}).encode()
                self._send(HTTPStatus.INTERNAL_SERVER_ERROR, body, "application/json")
                return
            body = json.dumps(controller.status(), ensure_ascii=False).encode()
            self._send(HTTPStatus.OK, body, "application/json; charset=utf-8")

        def log_message(self, _format: str, *_args: object) -> None:
            return

    return Handler


def _watchdog(controller: RunnerController, stop: threading.Event) -> None:
    while not stop.wait(10.0):
        controller.ensure_running()


def run_server(root: str | Path, *, host: str, port: int) -> None:
    """Serve the UI and keep the resumable wrapper alive while intent is running."""

    controller = RunnerController(RunnerPaths.from_root(root))
    stop = threading.Event()
    watchdog = threading.Thread(target=_watchdog, args=(controller, stop), daemon=True)
    watchdog.start()
    server = ThreadingHTTPServer((host, port), make_handler(controller))
    print(f"ISEF runner UI: http://{host}:{port}/")
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        stop.set()
        server.server_close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[2]
    run_server(root, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
