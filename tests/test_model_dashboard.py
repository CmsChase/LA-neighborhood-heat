from __future__ import annotations

import json
import re
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from la_heat.model_dashboard import (
    DashboardAlreadyRunningError,
    DashboardControlStore,
    DashboardProcessLock,
    ModelDashboardSupervisor,
    WorkerChangeError,
    _backoff_seconds,
    build_coordinator_command,
    create_server,
)
from la_heat.model_dashboard_watchdog import (
    WatchdogAlreadyRunningError,
    WatchdogProcessLock,
    build_dashboard_command,
    supervise_dashboard,
)
from la_heat.model_run_queue import ModelRunQueue, TaskSpec


class _FakeProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.exit_code: int | None = None

    def poll(self) -> int | None:
        return self.exit_code


def _wait_until(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not reached before timeout")


def _supervisor(
    tmp_path: Path,
    *,
    spawn: object,
    run_id: str | None = "run",
) -> ModelDashboardSupervisor:
    return ModelDashboardSupervisor(
        project_root=tmp_path,
        command=("python", "coordinator.py"),
        queue_path=tmp_path / "model_tasks.sqlite3",
        status_path=tmp_path / "status.json",
        control_path=tmp_path / "dashboard_control.json",
        workers=3,
        run_id=run_id,
        poll_seconds=0.01,
        initial_backoff_seconds=0.02,
        maximum_backoff_seconds=0.04,
        stable_run_seconds=10,
        spawn=spawn,  # type: ignore[arg-type]
    )


def test_control_intent_is_atomic_and_fail_closed(tmp_path: Path) -> None:
    store = DashboardControlStore(tmp_path / "control.json")
    assert store.get_desired_state() == "paused"
    store.set_desired_state("running")
    assert store.get_desired_state() == "running"
    payload = json.loads((tmp_path / "control.json").read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert not list(tmp_path.glob("*.tmp"))

    (tmp_path / "control.json").write_text("{broken", encoding="utf-8")
    assert store.get_desired_state() == "paused"


def test_worker_choice_is_persisted_without_changing_control_intent(
    tmp_path: Path,
) -> None:
    store = DashboardControlStore(tmp_path / "control.json")
    store.set_desired_state("running")
    store.set_workers(6)
    assert store.get_desired_state() == "running"
    assert store.get_workers() == 6

    store.set_desired_state("paused")
    assert store.get_desired_state() == "paused"
    assert store.get_workers() == 6

    with pytest.raises(ValueError, match="between 1 and 8"):
        store.set_workers(0)


def test_workers_can_change_only_while_fully_paused_and_apply_to_next_start(
    tmp_path: Path,
) -> None:
    spawned_commands: list[tuple[str, ...]] = []

    def spawn(command: tuple[str, ...], _cwd: Path) -> _FakeProcess:
        spawned_commands.append(command)
        return _FakeProcess(500 + len(spawned_commands))

    supervisor = _supervisor(tmp_path, spawn=spawn, run_id=None)
    changed = supervisor.set_workers(6)
    assert changed["workers"] == 6
    assert supervisor.command[-2:] == ("--workers", "6")

    supervisor.start_or_resume()
    try:
        _wait_until(lambda: len(spawned_commands) == 1)
        assert spawned_commands[0][-2:] == ("--workers", "6")
        with pytest.raises(WorkerChangeError, match="Safe Pause"):
            supervisor.set_workers(8)
    finally:
        supervisor.request_pause()
        supervisor.shutdown()

    restarted = _supervisor(
        tmp_path,
        spawn=lambda _command, _cwd: _FakeProcess(999),
        run_id=None,
    )
    try:
        assert restarted.snapshot()["workers"] == 6
        restarted.set_workers(8)
        assert restarted.snapshot()["workers"] == 8
        assert restarted.command[-2:] == ("--workers", "8")
    finally:
        restarted.shutdown()


def test_transferred_source_cannot_start_dashboard(tmp_path: Path) -> None:
    (tmp_path / "RUN_DISABLED_TRANSFERRED_OUT.txt").write_text(
        "transferred",
        encoding="utf-8",
    )
    supervisor = _supervisor(
        tmp_path,
        spawn=lambda _command, _cwd: _FakeProcess(1),
        run_id=None,
    )
    try:
        with pytest.raises(PermissionError, match="portable target"):
            supervisor.start_or_resume()
        assert supervisor.snapshot()["desired_state"] == "paused"
    finally:
        supervisor.shutdown()


def test_start_pause_persists_queue_state_and_drains_active_claim(
    tmp_path: Path,
) -> None:
    queue_path = tmp_path / "model_tasks.sqlite3"
    queue = ModelRunQueue(queue_path)
    queue.initialize_run(
        "run",
        [TaskSpec("fit-1", "inner_fit", {"fold": 1})],
        desired_state="paused",
    )
    spawned: list[_FakeProcess] = []

    def spawn(_command: tuple[str, ...], _cwd: Path) -> _FakeProcess:
        process = _FakeProcess(100 + len(spawned))
        spawned.append(process)
        return process

    supervisor = _supervisor(tmp_path, spawn=spawn)
    supervisor.begin_supervision()
    try:
        time.sleep(0.04)
        assert spawned == []
        started = supervisor.start_or_resume()
        assert started["desired_state"] == "running"
        _wait_until(lambda: len(spawned) == 1)
        assert queue.get_desired_state("run") == "running"

        claim = queue.claim_next("run", owner="worker-1", lease_seconds=60)
        assert claim is not None
        paused = supervisor.request_pause()
        assert paused["desired_state"] == "paused"
        assert paused["state"] == "pausing"
        assert queue.get_desired_state("run") == "paused"
        assert spawned[0].poll() is None  # Pause never terminates the active child.

        queue.complete(
            "run",
            claim.task_id,
            owner="worker-1",
            generation=claim.claim_generation,
            result={"ok": True},
        )
        spawned[0].exit_code = 0
        _wait_until(lambda: not supervisor.snapshot()["coordinator_running"])
        time.sleep(0.06)
        assert len(spawned) == 1
        assert supervisor.snapshot()["state"] == "paused"
    finally:
        supervisor.shutdown()

    restarted_spawns: list[_FakeProcess] = []
    restarted = _supervisor(
        tmp_path,
        spawn=lambda _command, _cwd: restarted_spawns.append(_FakeProcess(999)),
    )
    restarted.begin_supervision()
    try:
        time.sleep(0.05)
        assert restarted_spawns == []
    finally:
        restarted.shutdown()


def test_coordinator_auto_restarts_with_bounded_backoff(tmp_path: Path) -> None:
    spawned: list[_FakeProcess] = []

    def spawn(_command: tuple[str, ...], _cwd: Path) -> _FakeProcess:
        process = _FakeProcess(200 + len(spawned))
        spawned.append(process)
        return process

    supervisor = _supervisor(tmp_path, spawn=spawn, run_id=None)
    try:
        supervisor.start_or_resume()
        _wait_until(lambda: len(spawned) == 1)
        spawned[0].exit_code = 9
        _wait_until(lambda: len(spawned) == 2)
        spawned[1].exit_code = 9
        _wait_until(lambda: len(spawned) == 3)
        snapshot = supervisor.snapshot()
        assert snapshot["automatic_restart_count"] == 2
        assert snapshot["coordinator_running"] is True
        assert _backoff_seconds(1, initial_seconds=2, maximum_seconds=5) == 2
        assert _backoff_seconds(2, initial_seconds=2, maximum_seconds=5) == 4
        assert _backoff_seconds(99, initial_seconds=2, maximum_seconds=5) == 5
    finally:
        supervisor.request_pause()
        supervisor.shutdown()


def test_snapshot_passes_through_sanitized_status_summary(tmp_path: Path) -> None:
    status = {
        "run_id": "hash-run",
        "state": "complete",
        "counts": {
            "pending": 0,
            "running": 0,
            "complete": 8,
            "quarantined": 0,
            "total": 8,
        },
        "counts_by_kind": {"inner_fit": {"complete": 8, "total": 8}},
        "active_tasks": [],
        "workers": 4,
        "eta_seconds": 0,
        "events": [{"at": "now", "message": "done"}],
        "error": {"type": "ValueError", "message": "secret must not pass"},
    }
    (tmp_path / "status.json").write_text(json.dumps(status), encoding="utf-8")
    supervisor = _supervisor(
        tmp_path,
        spawn=lambda _command, _cwd: _FakeProcess(1),
        run_id=None,
    )
    snapshot = supervisor.snapshot()
    assert snapshot["run_id"] == "hash-run"
    assert snapshot["state"] == "complete"
    assert snapshot["completed"] == 8
    assert snapshot["counts_by_kind"] == status["counts_by_kind"]
    assert snapshot["workers"] == 3
    assert snapshot["error"] == {"type": "ValueError"}
    assert "secret" not in str(snapshot)


def test_local_http_server_serves_status_and_token_protected_controls(
    tmp_path: Path,
) -> None:
    class FakeSupervisor:
        workers = 4

        def snapshot(self) -> dict[str, object]:
            return {
                "state": "paused",
                "completed": 2,
                "total": 10,
                "workers": self.workers,
            }

        def start_or_resume(self) -> dict[str, object]:
            return {"state": "running", "completed": 2, "total": 10}

        def request_pause(self) -> dict[str, object]:
            return {"state": "pausing", "completed": 2, "total": 10}

        def set_workers(self, workers: int) -> dict[str, object]:
            if not 1 <= workers <= 8:
                raise ValueError("workers must be between 1 and 8")
            self.workers = workers
            return self.snapshot()

    page = tmp_path / "index.html"
    page.write_text(
        '<script>async function action(path) { return fetch(path, { method: "POST" }); }'
        "</script>",
        encoding="utf-8",
    )
    server = create_server(
        host="127.0.0.1",
        port=0,
        supervisor=FakeSupervisor(),  # type: ignore[arg-type]
        page_path=page,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        html = requests.get(base, timeout=5).text
        token = re.search(r'const __ISEF_CONTROL_TOKEN = "([^"]+)"', html)
        assert token is not None
        assert "X-ISEF-Control" in html
        assert requests.get(f"{base}/api/status", timeout=5).json()["state"] == "paused"
        assert requests.post(f"{base}/api/start", timeout=5).status_code == 403
        started = requests.post(
            f"{base}/api/start",
            headers={"X-ISEF-Control": token.group(1)},
            timeout=5,
        )
        assert started.status_code == 200
        assert started.json()["state"] == "running"
        paused = requests.post(
            f"{base}/api/pause",
            headers={"X-ISEF-Control": token.group(1)},
            timeout=5,
        )
        assert paused.json()["state"] == "pausing"
        changed = requests.post(
            f"{base}/api/workers",
            headers={"X-ISEF-Control": token.group(1)},
            json={"workers": 8},
            timeout=5,
        )
        assert changed.status_code == 200
        assert changed.json()["workers"] == 8
        invalid = requests.post(
            f"{base}/api/workers",
            headers={"X-ISEF-Control": token.group(1)},
            json={"workers": 12},
            timeout=5,
        )
        assert invalid.status_code == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_dashboard_and_watchdog_commands_are_explicit(tmp_path: Path) -> None:
    coordinator = build_coordinator_command(
        tmp_path,
        workers=6,
        coordinator_script="scripts/run_grouped_models.py",
    )
    assert coordinator[-2:] == ("--workers", "6")
    assert coordinator[1] == str((tmp_path / "scripts/run_grouped_models.py").resolve())

    dashboard = build_dashboard_command(
        tmp_path,
        workers=6,
        host="127.0.0.1",
        port=8765,
        no_browser=True,
        run_id="run-hash",
    )
    assert dashboard[1] == str(tmp_path.resolve() / "scripts/model_dashboard.py")
    assert dashboard[-3:] == ("--run-id", "run-hash", "--no-browser")
    assert "--workers" in dashboard


def test_watchdog_restarts_nonzero_children_with_capped_delays(tmp_path: Path) -> None:
    exits = deque([7, 8, 0])
    delays: list[float] = []
    events: list[str] = []

    def spawn(_command: tuple[str, ...], _cwd: Path) -> object:
        return SimpleNamespace(wait=lambda: exits.popleft())

    result = supervise_dashboard(
        tmp_path,
        spawn=spawn,  # type: ignore[arg-type]
        sleep=delays.append,
        emit=events.append,
        initial_backoff_seconds=2,
        maximum_backoff_seconds=3,
        no_browser=True,
    )
    assert result == 0
    assert delays == [2, 3]
    assert [event.split()[1] for event in events] == [
        "type=nonzero",
        "type=nonzero",
        "type=zero",
    ]


def test_dashboard_and_watchdog_locks_are_exclusive_and_released(tmp_path: Path) -> None:
    dashboard_lock = tmp_path / "dashboard.lock"
    with DashboardProcessLock(dashboard_lock):
        with pytest.raises(DashboardAlreadyRunningError, match="owns the server lock"):
            with DashboardProcessLock(dashboard_lock):
                pass
    with DashboardProcessLock(dashboard_lock):
        assert dashboard_lock.exists()

    watchdog_lock = tmp_path / "watchdog.lock"
    with WatchdogProcessLock(watchdog_lock):
        with pytest.raises(WatchdogAlreadyRunningError, match="owns"):
            with WatchdogProcessLock(watchdog_lock):
                pass
    with WatchdogProcessLock(watchdog_lock):
        assert watchdog_lock.exists()
