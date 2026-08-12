from __future__ import annotations

import re
import threading
import time
from pathlib import Path

import requests

from la_heat.model_run_queue import ModelRunQueue
from la_heat.multicity.external_target_dashboard import (
    DATABASE_RELATIVE_PATH,
    DEFAULT_PORT,
    EXTERNAL_TOTAL,
    ExternalTargetSupervisor,
    build_worker_command,
    create_server,
)
from la_heat.multicity.target_runtime import task_specs_from_target_plan
from la_heat.multicity.target_transaction import stage_multicity_target_build_plan

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class _Process:
    pid = 1234
    exit_code: int | None = None

    def poll(self) -> int | None:
        return self.exit_code


def _queue(root: Path) -> tuple[ModelRunQueue, str]:
    plan = stage_multicity_target_build_plan(PROJECT_ROOT, check_only=True)
    queue = ModelRunQueue(root / DATABASE_RELATIVE_PATH)
    run_id = "external-dashboard-test"
    queue.initialize_run(
        run_id,
        task_specs_from_target_plan(plan),
        desired_state="paused",
    )
    queue.set_desired_state(run_id, "running")
    for index in range(91):
        task = queue.claim_next(
            run_id,
            owner="source-fixture",
            lease_seconds=30,
            kinds={"source_overpass", "source_compile"},
        )
        assert task is not None
        queue.complete(
            run_id,
            task.task_id,
            owner="source-fixture",
            generation=task.claim_generation,
            result={"commit_sha256": f"{index:064x}"},
        )
    queue.set_desired_state(run_id, "paused")
    return queue, run_id


def test_worker_command_and_initial_snapshot_are_external_only(tmp_path: Path) -> None:
    assert DEFAULT_PORT == 8771
    assert build_worker_command(tmp_path, workers=4)[-5:] == (
        "--project-root",
        str(tmp_path.resolve()),
        "--workers",
        "4",
        "--start",
    )
    snapshot = ExternalTargetSupervisor(tmp_path).snapshot()
    assert snapshot["total"]["total"] == EXTERNAL_TOTAL
    assert snapshot["final_merge_sealed"] is True
    assert snapshot["target_values_opened_by_dashboard"] is False


def test_start_authenticates_and_pause_controls_only_queue_state(tmp_path: Path) -> None:
    queue, run_id = _queue(tmp_path)
    calls: list[str] = []
    process = _Process()

    supervisor = ExternalTargetSupervisor(
        tmp_path,
        spawn=lambda *_args: process,
        authorize=lambda _root: calls.append("authorize"),
        initialize=lambda _root: {"run_id": run_id},
        poll_seconds=0.005,
    )
    try:
        started = supervisor.start_or_continue(workers=3)
        assert calls == ["authorize"]
        assert queue.get_desired_state(run_id) == "running"
        assert started["workers"] == 3
        deadline = time.monotonic() + 2
        while not supervisor.snapshot()["worker_running"] and time.monotonic() < deadline:
            time.sleep(0.01)
        paused = supervisor.request_pause()
        assert queue.get_desired_state(run_id) == "paused"
        assert paused["desired_state"] == "paused"
        final = [task for task in queue.list_tasks(run_id) if task.kind == "final_merge"]
        assert len(final) == 1 and final[0].status == "pending" and final[0].attempt == 0
        assert not list(tmp_path.rglob("VALUES_OPENED.json"))
    finally:
        supervisor.close()


def test_unexpected_exit_is_restarted_without_touching_final_merge(tmp_path: Path) -> None:
    queue, run_id = _queue(tmp_path)
    processes: list[_Process] = []

    def spawn(*_args: object) -> _Process:
        process = _Process()
        processes.append(process)
        return process

    supervisor = ExternalTargetSupervisor(
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
        final = [task for task in queue.list_tasks(run_id) if task.kind == "final_merge"]
        assert final[0].status == "pending" and final[0].attempt == 0
    finally:
        supervisor.request_pause()
        supervisor.close()


def test_http_ui_has_three_city_progress_and_protected_controls(tmp_path: Path) -> None:
    queue, run_id = _queue(tmp_path)
    supervisor = ExternalTargetSupervisor(
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
        assert "Phoenix · Houston · Chicago" in page.text
        assert 'join("\\n")' in page.text
        token_match = re.search(r'const token="([^"]+)"', page.text)
        assert token_match is not None
        assert requests.post(
            f"{base}/api/start", json={"workers": 1}, timeout=5
        ).status_code == 403
        started = requests.post(
            f"{base}/api/start",
            headers={"X-Target-Control": token_match.group(1)},
            json={"workers": 2},
            timeout=5,
        )
        assert started.status_code == 200
        status = requests.get(f"{base}/api/status", timeout=5).json()
        assert status["total"]["total"] == 67
        assert status["final_merge_sealed"] is True
        assert set(status["cities"]) == {"phoenix_az", "houston_tx", "chicago_il"}
    finally:
        supervisor.request_pause()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        supervisor.close()
        assert queue.get_desired_state(run_id) == "paused"

