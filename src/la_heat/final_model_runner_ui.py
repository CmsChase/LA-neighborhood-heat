# ruff: noqa: E501
"""Local start/pause/resume UI for the prepared full-development final fit."""

from __future__ import annotations

import argparse
import json
import ntpath
import os
import shlex
import statistics
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Final

from la_heat.provenance import atomic_json

DEFAULT_HOST: Final = "127.0.0.1"
DEFAULT_PORT: Final = 8766
TOTAL_TUNING_TASKS: Final = 65
CONTROL_FILENAME: Final = ".final_model_runner_control.json"
MAX_STALLED_RESTARTS: Final = 4
RESTART_BASE_DELAY_SECONDS: Final = 10.0
RESTART_MAX_DELAY_SECONDS: Final = 120.0


class FinalRunnerError(RuntimeError):
    """Raised when final-model runner control fails."""


@dataclass(frozen=True)
class ProcessDiscovery:
    """Tri-state process discovery result; errors never mean "no process"."""

    ok: bool
    processes: tuple[dict[str, Any], ...] = ()
    error: str | None = None


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


def _output_root(root: Path) -> Path:
    return root / "data/interim/final_model_staging"


def _control_path(root: Path) -> Path:
    return _output_root(root) / CONTROL_FILENAME


def read_control(root: str | Path) -> dict[str, Any]:
    """Default to paused so merely opening the UI can never begin fitting."""

    path = _control_path(Path(root).resolve())
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"desired_state": "paused", "updated_at_utc": None}
    except (OSError, json.JSONDecodeError):
        return {"desired_state": "paused", "updated_at_utc": None}
    if not isinstance(payload, dict) or payload.get("desired_state") not in {
        "running",
        "paused",
    }:
        return {"desired_state": "paused", "updated_at_utc": None}
    return payload


def write_control(root: str | Path, desired_state: str) -> dict[str, Any]:
    if desired_state not in {"running", "paused"}:
        raise ValueError("desired_state must be running or paused")
    project = Path(root).resolve()
    payload = {
        "schema_version": 1,
        "desired_state": desired_state,
        "updated_at_utc": datetime.now(UTC).isoformat(),
    }
    _output_root(project).mkdir(parents=True, exist_ok=True)
    atomic_json(payload, _control_path(project))
    return payload


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def current_run(root: str | Path) -> tuple[Path | None, dict[str, Any]]:
    runs = _output_root(Path(root).resolve()) / "runs"
    manifests = list(runs.glob("*/final_model_run_manifest.json")) if runs.is_dir() else []
    if not manifests:
        return None, {}
    path = max(manifests, key=lambda item: item.stat().st_mtime)
    return path.parent, _read_json(path)


def model_lock_stage_status(root: str | Path) -> dict[str, Any]:
    project = Path(root).resolve()
    path = project / "manifests/model_lock/MODEL_LOCK_STAGING.json"
    payload = _read_json(path)
    formal_path = project / "manifests/model_lock/MODEL_LOCK.json"
    formal = _read_json(formal_path)
    blockers = payload.get("blockers")
    return {
        "exists": path.is_file() and bool(payload),
        "state": payload.get("state", "not_run"),
        "ready_for_formal_model_lock": payload.get("ready_for_formal_model_lock") is True,
        "formal_model_lock_written": payload.get("formal_model_lock_written") is True,
        "blockers": blockers if isinstance(blockers, list) else [],
        "commit_sha256": payload.get("commit_sha256"),
        "formal_lock_exists": formal_path.is_file() and bool(formal),
        "formal_lock_state": formal.get("state", "not_created"),
        "formal_lock_commit_sha256": formal.get("commit_sha256"),
        "formal_lock_keeps_2025_locked": formal.get("final_test_locked") is True
        and formal.get("final_test_values_read") is False,
    }


def _estimate(fragment_paths: Sequence[Path], remaining: int) -> str:
    if remaining <= 0:
        return "正在最终拟合或已完成"
    if len(fragment_paths) < 3:
        return "完成 3 个任务后开始估算"
    recent = sorted(fragment_paths, key=lambda item: item.stat().st_mtime)[-16:]
    times = [item.stat().st_mtime for item in recent]
    intervals = [
        right - left
        for left, right in zip(times, times[1:], strict=False)
        if 2.0 <= right - left <= 30.0 * 60.0
    ]
    if not intervals:
        return "估算中"
    seconds = statistics.median(intervals) * remaining
    minutes = max(1, round(seconds / 60.0))
    return f"约 {minutes} 分钟" if minutes < 60 else f"约 {minutes / 60.0:.1f} 小时"


_DISCOVERY_SCRIPT: Final = r"""
$items = @(Get-CimInstance Win32_Process | Where-Object {
  $_.Name -in @('python.exe', 'pythonw.exe')
} | Select-Object ProcessId, ParentProcessId, CreationDate, ExecutablePath, CommandLine)
ConvertTo-Json -InputObject $items -Compress
"""


def _normal_path(value: str) -> str:
    return ntpath.normcase(ntpath.normpath(value.strip().strip('"')))


def _command_args(command_line: str) -> tuple[str, ...]:
    try:
        return tuple(part.strip('"') for part in shlex.split(command_line, posix=False))
    except ValueError:
        return ()


def is_exact_target_process(row: dict[str, Any], root: str | Path) -> bool:
    """Accept only the exact venv/script/config process owned by this project."""

    project = Path(root).resolve()
    expected_python = project / ".venv/Scripts/python.exe"
    expected_script = project / "scripts/build_final_models.py"
    expected_config = project / "configs/final_model.toml"
    executable = row.get("ExecutablePath")
    command_line = row.get("CommandLine")
    args = _command_args(command_line) if isinstance(command_line, str) else ()
    return bool(
        isinstance(row.get("ProcessId"), int)
        and row.get("CreationDate")
        and isinstance(executable, str)
        and _normal_path(executable) == _normal_path(str(expected_python))
        and len(args) == 4
        and _normal_path(args[0]) == _normal_path(str(expected_python))
        and _normal_path(args[1]) == _normal_path(str(expected_script))
        and args[2] == "--config"
        and _normal_path(args[3]) == _normal_path(str(expected_config))
    )


def discover_processes(root: str | Path) -> ProcessDiscovery:
    if os.name != "nt":
        return ProcessDiscovery(ok=True)
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", _DISCOVERY_SCRIPT],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return ProcessDiscovery(ok=False, error=type(error).__name__)
    if result.returncode != 0 or not result.stdout.strip():
        return ProcessDiscovery(ok=False, error=f"PowerShellExit{result.returncode}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return ProcessDiscovery(ok=False, error="InvalidProcessJson")
    rows = payload if isinstance(payload, list) else [payload]
    if not all(isinstance(row, dict) for row in rows):
        return ProcessDiscovery(ok=False, error="InvalidProcessRows")
    exact = tuple(row for row in rows if is_exact_target_process(row, root))
    return ProcessDiscovery(ok=True, processes=exact)


def _process_key(row: dict[str, Any]) -> tuple[int, str, str]:
    return (
        int(row["ProcessId"]),
        str(row.get("CreationDate", "")),
        str(row.get("CommandLine", "")),
    )


def stop_processes(root: str | Path, processes: Sequence[dict[str, Any]]) -> None:
    if os.name != "nt":
        raise FinalRunnerError("Pause is currently implemented for Windows only.")
    # Re-query immediately before termination. PID + creation time + exact command
    # line must still match, preventing broad-name matches and ordinary PID reuse.
    rechecked = discover_processes(root)
    if not rechecked.ok:
        raise FinalRunnerError(f"Process recheck failed: {rechecked.error}")
    requested = {_process_key(row) for row in processes}
    ids = sorted(
        {
            int(row["ProcessId"])
            for row in rechecked.processes
            if _process_key(row) in requested
        },
        reverse=True,
    )
    if not ids:
        return
    script = "Stop-Process -Id @(" + ",".join(map(str, ids)) + ") -Force -ErrorAction SilentlyContinue"
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", script],
        capture_output=True,
        check=False,
        timeout=15,
    )
    if result.returncode != 0:
        raise FinalRunnerError(f"Stop-Process failed with exit {result.returncode}")


def launch_build(root: Path) -> int:
    python = root / ".venv/Scripts/python.exe"
    script = root / "scripts/build_final_models.py"
    config = root / "configs/final_model.toml"
    if not python.is_file() or not script.is_file() or not config.is_file():
        raise FinalRunnerError("Final-model runner inputs are missing.")
    stdout_path = root / "reports/runtime/final_model_runner.stdout.log"
    stderr_path = root / "reports/runtime/final_model_runner.stderr.log"
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    with stdout_path.open("a", encoding="utf-8") as stdout, stderr_path.open(
        "a", encoding="utf-8"
    ) as stderr:
        child = subprocess.Popen(  # noqa: S603
            [str(python), str(script), "--config", str(config)],
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            close_fds=True,
            creationflags=flags,
        )
    return int(child.pid)


class FinalModelController:
    def __init__(
        self,
        root: str | Path,
        *,
        discover: Callable[[str | Path], ProcessDiscovery] = discover_processes,
        stop: Callable[[str | Path, Sequence[dict[str, Any]]], None] = stop_processes,
        launch: Callable[[Path], int] = launch_build,
    ) -> None:
        self.root = Path(root).resolve()
        self._discover = discover
        self._stop = stop
        self._launch = launch
        self._lock = threading.Lock()
        self._session_armed = False
        self._last_launched_pid: int | None = None
        self._last_launch_fragments: int | None = None
        self._stalled_restarts = 0
        self._next_launch_at = 0.0
        self.last_action: str | None = None
        self.last_error: str | None = None

    def _complete(self) -> bool:
        run, _ = current_run(self.root)
        return run is not None and (run / "final_model_build_provenance.json").is_file()

    def _fragment_count(self) -> int:
        run, _ = current_run(self.root)
        if run is None:
            return 0
        return min(
            len(list((run / "tuning_fragments").glob("*.json"))),
            TOTAL_TUNING_TASKS,
        )

    def begin_session(self) -> None:
        """Every newly opened UI session starts disarmed and paused."""

        self.pause(action="新 UI 会话已安全暂停；需手动点击“开始 / 继续”")

    def _schedule_retry_or_pause(self, *, reason: str) -> None:
        self._stalled_restarts += 1
        if self._stalled_restarts >= MAX_STALLED_RESTARTS:
            self._session_armed = False
            write_control(self.root, "paused")
            self.last_action = "已自动暂停"
            self.last_error = f"{reason}；连续 {MAX_STALLED_RESTARTS} 次无进度"
            return
        delay = min(
            RESTART_MAX_DELAY_SECONDS,
            RESTART_BASE_DELAY_SECONDS * 2 ** max(0, self._stalled_restarts - 1),
        )
        self._next_launch_at = time.monotonic() + delay
        self.last_action = f"{reason}；将在约 {round(delay)} 秒后自动续跑"

    def ensure_running(self) -> None:
        with self._lock:
            if (
                not self._session_armed
                or read_control(self.root)["desired_state"] != "running"
                or self._complete()
            ):
                return
            discovery = self._discover(self.root)
            if not discovery.ok:
                self.last_error = f"进程检查失败（{discovery.error}）；为安全起见未启动"
                return
            if discovery.processes:
                return

            now = time.monotonic()
            if self._last_launched_pid is not None:
                completed = self._fragment_count()
                if self._last_launch_fragments is not None and completed > self._last_launch_fragments:
                    self._stalled_restarts = 0
                self._last_launched_pid = None
                self._schedule_retry_or_pause(reason="任务退出")
                return
            if now < self._next_launch_at:
                return
            try:
                pid = self._launch(self.root)
                self._last_launched_pid = pid
                self._last_launch_fragments = self._fragment_count()
                self.last_action = f"已启动 PID {pid}"
                self.last_error = None
            except (OSError, FinalRunnerError) as error:
                self._schedule_retry_or_pause(reason=f"启动失败（{type(error).__name__}）")

    def pause(self, *, action: str | None = None) -> None:
        with self._lock:
            self._session_armed = False
            write_control(self.root, "paused")
            discovery = self._discover(self.root)
            if not discovery.ok:
                self.last_action = action or "已写入暂停状态"
                self.last_error = f"进程检查失败（{discovery.error}）；无法确认是否仍有进程"
                return
            try:
                self._stop(self.root, discovery.processes)
            except (OSError, FinalRunnerError) as error:
                self.last_action = action or "已写入暂停状态"
                self.last_error = f"停止进程失败（{type(error).__name__}）"
                return
            self.last_action = action or f"已暂停；停止 {len(discovery.processes)} 个精确匹配进程"
            self.last_error = None

    def resume(self) -> None:
        with self._lock:
            write_control(self.root, "running")
            self._session_armed = True
            self._last_launched_pid = None
            self._last_launch_fragments = None
            self._stalled_restarts = 0
            self._next_launch_at = 0.0
            self.last_action = "已请求开始/继续"
            self.last_error = None
        self.ensure_running()

    def status(self) -> dict[str, Any]:
        run, manifest = current_run(self.root)
        fragments = [] if run is None else list((run / "tuning_fragments").glob("*.json"))
        completed = min(len(fragments), TOTAL_TUNING_TASKS)
        discovery = self._discover(self.root)
        complete = self._complete()
        selected = run is not None and (run / "full_development_selections.json").is_file()
        model_count = 0 if run is None else sum(
            (run / f"{model}_full_development.joblib").is_file() for model in ("B1", "M2")
        )
        return {
            "schema_version": 1,
            "updated_at": datetime.now().astimezone().isoformat(),
            "run_id": manifest.get("run_id"),
            "prepared": manifest.get("state") == "prepared_development_only",
            "desired_state": read_control(self.root)["desired_state"],
            "session_armed": self._session_armed,
            "process_discovery_ok": discovery.ok,
            "process_discovery_error": discovery.error,
            "running": bool(discovery.processes) if discovery.ok else False,
            "process_ids": [int(row["ProcessId"]) for row in discovery.processes],
            "tuning_completed": completed,
            "tuning_total": TOTAL_TUNING_TASKS,
            "percent": round(100.0 * completed / TOTAL_TUNING_TASKS, 1),
            "eta": "已完成" if complete else _estimate(
                fragments, TOTAL_TUNING_TASKS - completed
            ),
            "selection_complete": selected,
            "final_models_complete": model_count,
            "final_models_total": 2,
            "complete": complete,
            "final_test_locked": True,
            "model_lock_stage": model_lock_stage_status(self.root),
            "last_action": self.last_action,
            "last_error": self.last_error,
        }


HTML: Final = r"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>LA Heat 最终模型任务</title><style>body{font:16px system-ui;margin:0;background:#111;color:#eee}main{max-width:780px;margin:30px auto;padding:18px}.card{background:#1d1d1d;border:1px solid #383838;border-radius:12px;padding:18px;margin-bottom:16px}.row{display:flex;justify-content:space-between;gap:12px}.big{font-size:28px;font-weight:700}.bar{height:14px;background:#3a3a3a;border-radius:8px;overflow:hidden;margin:12px 0}.fill{height:100%;background:#3182f6}button{font-size:16px;padding:10px 18px;margin:8px 8px 0 0;border:0;border-radius:8px;cursor:pointer}.pause{background:#ffcc33}.resume{background:#2ecc71}.muted{color:#aaa;font-size:14px}.error{color:#ff8a8a}.ok{color:#71dc91}.blocker{padding:10px;background:#342626;border-radius:8px}</style></head><body><main><h1>LA Heat 项目运行状态</h1><div id="root">读取中…</div></main><script>const esc=s=>String(s??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));const blockerName=b=>b==='git_head_missing'?'尚未创建 Git 首次提交':b;async function action(n){await fetch('/api/'+n,{method:'POST'});refresh()}async function refresh(){const s=await(await fetch('/api/status')).json(),m=s.model_lock_stage||{};document.querySelector('#root').innerHTML=`<section class="card"><div class="row"><b>${s.complete?'最终模型：已完成':s.running?'最终模型：运行中':s.desired_state==='paused'||!s.session_armed?'最终模型：未启动/已暂停':'最终模型：等待自动续跑'}</b><span>2025 锁定</span></div><div class="big">调参 ${s.tuning_completed} / ${s.tuning_total}</div><div class="bar"><div class="fill" style="width:${s.percent}%"></div></div><div>预计剩余：${esc(s.eta)}　最终拟合：${s.final_models_complete}/2</div><button class="pause" onclick="action('pause')">暂停</button><button class="resume" onclick="action('resume')">开始 / 继续</button><p class="muted">模型完成后无需再次点击开始。<br>${esc(s.last_action||'')}</p>${s.last_error||!s.process_discovery_ok?`<p class="error">${esc(s.last_error||s.process_discovery_error)}</p>`:''}</section><section class="card"><div class="row"><b>模型锁</b><span class="${m.formal_lock_exists||m.ready_for_formal_model_lock?'ok':'error'}">${m.formal_lock_exists?'正式锁已建立':!m.exists?'尚未审计':m.ready_for_formal_model_lock?'资格审计通过，等待正式锁':'资格审计有阻塞'}</span></div>${m.blockers?.length?`<p class="blocker">阻塞原因：${m.blockers.map(blockerName).map(esc).join('；')}</p>`:m.formal_lock_exists?'<p class="ok">模型、配置、数据拆分和特征均已正式冻结。</p>':'<p class="ok">没有资格审计阻塞项。</p>'}<p class="muted">正式锁只冻结开发决策，不自动读取或评估 2025。</p></section>`}refresh();setInterval(refresh,3000)</script></body></html>"""


def make_handler(controller: FinalModelController) -> type[BaseHTTPRequestHandler]:
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
            elif self.path == "/api/status":
                self._send(
                    HTTPStatus.OK,
                    json.dumps(controller.status(), ensure_ascii=False).encode(),
                    "application/json; charset=utf-8",
                )
            else:
                self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")

        def do_POST(self) -> None:  # noqa: N802
            if self.path == "/api/pause":
                controller.pause()
            elif self.path == "/api/resume":
                controller.resume()
            else:
                self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
                return
            self._send(
                HTTPStatus.OK,
                json.dumps(controller.status(), ensure_ascii=False).encode(),
                "application/json; charset=utf-8",
            )

        def log_message(self, _format: str, *_args: object) -> None:
            return

    return Handler


def _watchdog(controller: FinalModelController, stop: threading.Event) -> None:
    while not stop.wait(10.0):
        try:
            controller.ensure_running()
        except Exception as error:  # pragma: no cover - last-resort thread guard
            controller.last_error = f"监控线程错误（{type(error).__name__}）"


def run_server(root: str | Path, *, host: str, port: int) -> None:
    controller = FinalModelController(root)
    controller.begin_session()
    stop = threading.Event()
    threading.Thread(target=_watchdog, args=(controller, stop), daemon=True).start()
    server = ThreadingHTTPServer((host, port), make_handler(controller))
    print(f"Final model runner UI: http://{host}:{port}/")
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        stop.set()
        server.server_close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--status-only", action="store_true")
    args = parser.parse_args(argv)
    root = _root()
    if args.status_only:
        print(json.dumps(FinalModelController(root).status(), ensure_ascii=False, indent=2))
        return 0
    run_server(root, host=args.host, port=args.port)
    return 0
