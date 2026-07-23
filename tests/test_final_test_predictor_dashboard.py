from __future__ import annotations

import json
import re
import sys
import threading
import time
from pathlib import Path

import pytest
import requests

from la_heat.final_test_predictor_dashboard import (
    DashboardAlreadyRunningError,
    DashboardProcessLock,
    FinalTestPredictorSupervisor,
    build_engine_command,
    create_server,
    normalize_engine_status,
)


class _FakeProcess:
    _next_pid = 100

    def __init__(self) -> None:
        type(self)._next_pid += 1
        self.pid = type(self)._next_pid
        self.exit_code: int | None = None

    def poll(self) -> int | None:
        return self.exit_code


def _wait_until(predicate: object, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if callable(predicate) and predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not reached")


def test_exact_engine_command_allows_only_6_or_8_workers(tmp_path: Path) -> None:
    assert build_engine_command(tmp_path, workers=8) == (
        sys.executable,
        str(
            (
                tmp_path.resolve()
                / "scripts"
                / "build_final_test_sentinel_features.py"
            ).resolve()
        ),
        "--workers",
        "8",
    )
    with pytest.raises(ValueError, match="6 or 8"):
        build_engine_command(tmp_path, workers=7)


def test_status_contract_is_exactly_67_and_redacts_signed_urls() -> None:
    status = normalize_engine_status(
        {
            "state": "running",
            "total": 999,
            "completed_ids": ["a", "b"],
            "active": [{"physical_acquisition_id": "c"}],
            "failures": ["d"],
            "events": [
                {
                    "at": "now",
                    "message": "read https://example.test/a.tif?token=secret",
                }
            ],
            "estimated_remaining_seconds": 123,
        }
    )
    assert status["total"] == 67
    assert status["completed"] == 2
    assert status["running"] == 1
    assert status["failed"] == 1
    assert status["current"] == ["c"]
    assert status["eta_seconds"] == 123
    assert "secret" not in status["log_tail"][0]
    assert status["status_contract_error"] == "engine total must remain 67"


def test_dashboard_is_idle_by_default_and_start_uses_exact_child(
    tmp_path: Path,
) -> None:
    launches: list[tuple[tuple[str, ...], Path, _FakeProcess]] = []

    def spawn(command: tuple[str, ...], cwd: Path) -> _FakeProcess:
        process = _FakeProcess()
        launches.append((command, cwd, process))
        return process

    supervisor = FinalTestPredictorSupervisor(
        tmp_path,
        spawn=spawn,
        poll_seconds=0.01,
    )
    try:
        assert launches == []
        assert supervisor.snapshot()["state"] == "not_started"
        snapshot = supervisor.start_or_continue(8)
        assert snapshot["state"] == "running"
        assert len(launches) == 1
        assert launches[0][0] == build_engine_command(tmp_path, workers=8)
        assert launches[0][1] == tmp_path.resolve()
    finally:
        supervisor.close()


def test_pause_is_persistent_and_never_kills_owned_or_unowned_process(
    tmp_path: Path,
) -> None:
    process = _FakeProcess()
    supervisor = FinalTestPredictorSupervisor(
        tmp_path,
        spawn=lambda _command, _cwd: process,
        poll_seconds=0.01,
    )
    try:
        supervisor.start_or_continue(6)
        paused = supervisor.request_pause()
        assert paused["pause_requested"] is True
        assert paused["state"] == "pausing"
        assert process.exit_code is None
        marker = (
            tmp_path
            / "data"
            / "interim"
            / "final_test_2025"
            / "sentinel"
            / "PAUSE_REQUESTED"
        )
        assert json.loads(marker.read_text(encoding="utf-8"))["intent"] == "pause"

        process.exit_code = 0
        _wait_until(lambda: supervisor.snapshot()["state"] == "paused")
        time.sleep(0.04)
        assert supervisor.snapshot()["automatic_restart_count"] == 0
    finally:
        supervisor.close()


def test_crashes_auto_restart_then_open_bounded_circuit(tmp_path: Path) -> None:
    processes: list[_FakeProcess] = []

    def spawn(_command: tuple[str, ...], _cwd: Path) -> _FakeProcess:
        process = _FakeProcess()
        processes.append(process)
        return process

    supervisor = FinalTestPredictorSupervisor(
        tmp_path,
        spawn=spawn,
        restart_delays=(0.01, 0.01),
        poll_seconds=0.005,
    )
    try:
        supervisor.start_or_continue(6)
        for expected_launches in (2, 3):
            processes[-1].exit_code = 9
            _wait_until(
                lambda expected=expected_launches: len(processes) >= expected
            )
        processes[-1].exit_code = 9
        _wait_until(lambda: supervisor.snapshot()["restart_circuit_open"])
        snapshot = supervisor.snapshot()
        assert len(processes) == 3
        assert snapshot["automatic_restart_count"] == 2
        assert snapshot["state"] == "restart_circuit_open"
    finally:
        supervisor.close()


def test_pause_wins_while_restart_is_pending(tmp_path: Path) -> None:
    processes: list[_FakeProcess] = []

    def spawn(_command: tuple[str, ...], _cwd: Path) -> _FakeProcess:
        process = _FakeProcess()
        processes.append(process)
        return process

    supervisor = FinalTestPredictorSupervisor(
        tmp_path,
        spawn=spawn,
        restart_delays=(0.2,),
        poll_seconds=0.005,
    )
    try:
        supervisor.start_or_continue(6)
        processes[0].exit_code = 5
        _wait_until(lambda: supervisor.snapshot()["state"] == "restarting")
        supervisor.request_pause()
        time.sleep(0.25)
        assert len(processes) == 1
        assert supervisor.snapshot()["state"] == "paused"
    finally:
        supervisor.close()


def test_dashboard_lock_is_exclusive_and_os_released(tmp_path: Path) -> None:
    lock_path = tmp_path / "dashboard.lock"
    with DashboardProcessLock(lock_path):
        with pytest.raises(DashboardAlreadyRunningError, match="already running"):
            with DashboardProcessLock(lock_path):
                pass
    with DashboardProcessLock(lock_path):
        assert lock_path.exists()


def test_http_controls_require_token_and_worker_choice(tmp_path: Path) -> None:
    launches: list[_FakeProcess] = []

    def spawn(_command: tuple[str, ...], _cwd: Path) -> _FakeProcess:
        process = _FakeProcess()
        launches.append(process)
        return process

    supervisor = FinalTestPredictorSupervisor(
        tmp_path,
        spawn=spawn,
        poll_seconds=0.01,
    )
    server = create_server(host="127.0.0.1", port=0, supervisor=supervisor)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        html = requests.get(base, timeout=5).text
        token = re.search(r'const token="([^"]+)"', html).group(1)  # type: ignore[union-attr]
        assert requests.post(f"{base}/api/start", timeout=5).status_code == 403
        bad = requests.post(
            f"{base}/api/start",
            headers={"X-Final-Test-Control": token},
            json={"workers": 7},
            timeout=5,
        )
        assert bad.status_code == 400
        started = requests.post(
            f"{base}/api/start",
            headers={"X-Final-Test-Control": token},
            json={"workers": 8},
            timeout=5,
        )
        assert started.status_code == 200
        assert started.json()["workers"] == 8
        assert len(launches) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        supervisor.close()


def test_http_server_rejects_non_loopback_binding(tmp_path: Path) -> None:
    supervisor = FinalTestPredictorSupervisor(tmp_path)
    try:
        with pytest.raises(ValueError, match="localhost"):
            create_server(host="0.0.0.0", port=8766, supervisor=supervisor)
    finally:
        supervisor.close()
