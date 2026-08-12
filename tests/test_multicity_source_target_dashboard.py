from __future__ import annotations

import re
import threading
import time
from pathlib import Path

import requests

from la_heat.model_run_queue import ModelRunQueue, TaskSpec
from la_heat.multicity.source_target_dashboard import (
    DATABASE_RELATIVE_PATH,
    DEFAULT_PORT,
    EXTERNAL_SEALED_TOTAL,
    SOURCE_TOTAL,
    SourceTargetSupervisor,
    build_worker_command,
    create_server,
)


class _Process:
    pid = 1234
    exit_code: int | None = None

    def poll(self) -> int | None:
        return self.exit_code


def _queue(root: Path) -> tuple[ModelRunQueue, str]:
    queue = ModelRunQueue(root / DATABASE_RELATIVE_PATH)
    run_id = "target-run"
    tasks = [
        *(
            TaskSpec(
                f"source-{i}", "source_overpass", {"city_id": "los_angeles_ca"}
            )
            for i in range(90)
        ),
        TaskSpec("source-compile", "source_compile", {"city_id": "los_angeles_ca"}),
        *(
            TaskSpec(
                f"external-{i}", "external_overpass", {"city_id": "external"}
            )
            for i in range(64)
        ),
        *(
            TaskSpec(
                f"external-compile-{i}",
                "external_compile",
                {"city_id": "external"},
            )
            for i in range(3)
        ),
        TaskSpec("merge", "final_merge", {}),
    ]
    queue.initialize_run(run_id, tasks, desired_state="paused")
    return queue, run_id


def test_worker_command_and_initial_snapshot_are_source_only(tmp_path: Path) -> None:
    assert DEFAULT_PORT == 8770
    assert build_worker_command(tmp_path, workers=2)[-5:] == (
        "--project-root",
        str(tmp_path.resolve()),
        "--workers",
        "2",
        "--start",
    )
    supervisor = SourceTargetSupervisor(tmp_path)
    snapshot = supervisor.snapshot()
    assert snapshot["source"]["total"] == SOURCE_TOTAL
    assert snapshot["external"]["total"] == EXTERNAL_SEALED_TOTAL
    assert snapshot["external_sealed"] is True
    assert snapshot["target_values_opened_by_dashboard"] is False


def test_start_authenticates_then_controls_queue_and_pause_drains(tmp_path: Path) -> None:
    queue, run_id = _queue(tmp_path)
    calls: list[str] = []
    process = _Process()

    def authorize(_root: Path) -> object:
        calls.append("authorize")
        return object()

    def initialize(_root: Path) -> dict[str, object]:
        calls.append("initialize")
        return {"run_id": run_id}

    supervisor = SourceTargetSupervisor(
        tmp_path,
        spawn=lambda *_args: process,
        authorize=authorize,
        initialize=initialize,
        poll_seconds=0.005,
    )
    try:
        started = supervisor.start_or_continue(workers=2)
        assert calls == ["authorize", "initialize"]
        assert queue.get_desired_state(run_id) == "running"
        assert started["workers"] == 2
        deadline = time.monotonic() + 2
        while not supervisor.snapshot()["worker_running"] and time.monotonic() < deadline:
            time.sleep(0.01)
        paused = supervisor.request_pause()
        assert queue.get_desired_state(run_id) == "paused"
        assert process.exit_code is None
        assert paused["desired_state"] == "paused"
        assert not list(tmp_path.rglob("VALUES_OPENED.json"))
    finally:
        supervisor.close()


def test_unexpected_exit_restarts_while_running(tmp_path: Path) -> None:
    queue, run_id = _queue(tmp_path)
    processes: list[_Process] = []

    def spawn(*_args: object) -> _Process:
        process = _Process()
        processes.append(process)
        return process

    supervisor = SourceTargetSupervisor(
        tmp_path,
        spawn=spawn,
        authorize=lambda _root: object(),
        initialize=lambda _root: {"run_id": run_id},
        poll_seconds=0.003,
        initial_backoff_seconds=0.01,
        maximum_backoff_seconds=0.02,
    )
    try:
        supervisor.start_or_continue(workers=1)
        deadline = time.monotonic() + 2
        while len(processes) < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        processes[0].exit_code = 9
        deadline = time.monotonic() + 2
        while len(processes) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(processes) == 2
        assert supervisor.snapshot()["automatic_restart_count"] == 1
    finally:
        supervisor.request_pause()
        supervisor.close()


def test_http_ui_has_protected_controls(tmp_path: Path) -> None:
    queue, run_id = _queue(tmp_path)
    supervisor = SourceTargetSupervisor(
        tmp_path,
        spawn=lambda *_args: _Process(),
        authorize=lambda _root: object(),
        initialize=lambda _root: {"run_id": run_id},
    )
    server = create_server(host="127.0.0.1", port=0, supervisor=supervisor)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        page = requests.get(base, timeout=5)
        assert page.status_code == 200
        assert r'join("\n")' in page.text
        assert 'join("\n")' not in page.text
        assert "Los Angeles 训练目标构建" in page.text
        assert "外部测试组" in page.text
        token = re.search(r'const token="([^"]+)"', page.text).group(1)  # type: ignore[union-attr]
        assert requests.post(f"{base}/api/start", json={"workers": 1}, timeout=5).status_code == 403
        started = requests.post(
            f"{base}/api/start",
            headers={"X-Target-Control": token},
            json={"workers": 1},
            timeout=5,
        )
        assert started.status_code == 200
        status = requests.get(f"{base}/api/status", timeout=5).json()
        assert status["external_sealed"] is True
        assert status["source"]["total"] == 91
    finally:
        supervisor.request_pause()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        supervisor.close()
        assert queue.get_desired_state(run_id) == "paused"
