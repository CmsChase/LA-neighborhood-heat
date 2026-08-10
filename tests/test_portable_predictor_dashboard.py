from __future__ import annotations

import json
import re
import sys
import threading
import time
from pathlib import Path

import pytest
import requests

from la_heat.multicity.portable_predictor_dashboard import (
    CONTROL_FILENAME,
    DEFAULT_PORT,
    PAUSE_FILENAME,
    RUNTIME_RELATIVE_DIRECTORY,
    STATUS_FILENAME,
    DashboardAlreadyRunningError,
    DashboardProcessLock,
    PortablePredictorSupervisor,
    build_engine_command,
    create_server,
    normalize_engine_status,
)


class _FakeProcess:
    _next_pid = 2000

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


def _runtime(root: Path) -> Path:
    return root / RUNTIME_RELATIVE_DIRECTORY


def _write_status(root: Path, payload: dict[str, object]) -> None:
    runtime = _runtime(root)
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / STATUS_FILENAME).write_text(json.dumps(payload), encoding="utf-8")


def test_exact_engine_command_and_default_port(tmp_path: Path) -> None:
    assert DEFAULT_PORT == 8768
    assert build_engine_command(tmp_path) == (
        sys.executable,
        str(
            (
                tmp_path.resolve()
                / "scripts"
                / "build_portable_predictor_components.py"
            ).resolve()
        ),
    )


def test_fresh_runtime_auto_starts_with_d_drive_temp_and_memory_token(
    tmp_path: Path,
) -> None:
    secret = "secret-earthdata-value"
    launches: list[tuple[tuple[str, ...], Path, dict[str, str], _FakeProcess]] = []

    def spawn(
        command: tuple[str, ...],
        cwd: Path,
        environment: dict[str, str],
    ) -> _FakeProcess:
        process = _FakeProcess()
        launches.append((command, cwd, dict(environment), process))
        return process

    supervisor = PortablePredictorSupervisor(
        tmp_path,
        spawn=spawn,  # type: ignore[arg-type]
        poll_seconds=0.005,
        initial_backoff_seconds=0.01,
        maximum_backoff_seconds=0.02,
        parent_environment={"PATH": "synthetic", "NASA_EARTHDATA_TOKEN": secret},
    )
    try:
        supervisor.begin_supervision()
        _wait_until(lambda: len(launches) == 1)
        command, cwd, environment, _ = launches[0]
        assert command == build_engine_command(tmp_path)
        assert cwd == tmp_path.resolve()
        assert environment["EARTHDATA_TOKEN"] == secret
        assert "NASA_EARTHDATA_TOKEN" not in environment
        assert "EDL_TOKEN" not in environment
        expected_tmp = str((_runtime(tmp_path) / "tmp").resolve())
        assert environment["TMP"] == expected_tmp
        assert environment["TEMP"] == expected_tmp
        assert environment["TMPDIR"] == expected_tmp

        control = (_runtime(tmp_path) / CONTROL_FILENAME).read_text(encoding="utf-8")
        snapshot = json.dumps(supervisor.snapshot())
        assert '"desired_state": "running"' in control
        assert secret not in control
        assert secret not in snapshot
        assert secret not in " ".join(command)
    finally:
        supervisor.close()


def test_pause_is_cooperative_persistent_and_wins_after_dashboard_restart(
    tmp_path: Path,
) -> None:
    first_process = _FakeProcess()
    first_launches = 0

    def first_spawn(*_args: object) -> _FakeProcess:
        nonlocal first_launches
        first_launches += 1
        return first_process

    supervisor = PortablePredictorSupervisor(
        tmp_path,
        spawn=first_spawn,  # type: ignore[arg-type]
        poll_seconds=0.005,
        parent_environment={},
    )
    try:
        supervisor.begin_supervision()
        _wait_until(lambda: first_launches == 1)
        paused = supervisor.request_pause()
        assert paused["state"] == "pausing"
        assert first_process.exit_code is None
        marker = json.loads(
            (_runtime(tmp_path) / PAUSE_FILENAME).read_text(encoding="utf-8")
        )
        control = json.loads(
            (_runtime(tmp_path) / CONTROL_FILENAME).read_text(encoding="utf-8")
        )
        assert marker["intent"] == "pause"
        assert control["desired_state"] == "paused"
        first_process.exit_code = 0
        _wait_until(lambda: supervisor.snapshot()["state"] == "paused")
    finally:
        supervisor.close()

    restarted_launches = 0

    def restarted_spawn(*_args: object) -> _FakeProcess:
        nonlocal restarted_launches
        restarted_launches += 1
        return _FakeProcess()

    restarted = PortablePredictorSupervisor(
        tmp_path,
        spawn=restarted_spawn,  # type: ignore[arg-type]
        poll_seconds=0.005,
        parent_environment={},
    )
    try:
        restarted.begin_supervision()
        time.sleep(0.05)
        assert restarted_launches == 0
        assert restarted.snapshot()["state"] == "paused"
    finally:
        restarted.close()


def test_unexpected_exit_restarts_with_exponential_backoff(tmp_path: Path) -> None:
    processes: list[_FakeProcess] = []

    def spawn(*_args: object) -> _FakeProcess:
        process = _FakeProcess()
        processes.append(process)
        return process

    supervisor = PortablePredictorSupervisor(
        tmp_path,
        spawn=spawn,  # type: ignore[arg-type]
        poll_seconds=0.003,
        initial_backoff_seconds=0.01,
        maximum_backoff_seconds=0.02,
        stable_runtime_seconds=100,
        parent_environment={},
    )
    try:
        supervisor.begin_supervision()
        _wait_until(lambda: len(processes) == 1)
        processes[0].exit_code = 9
        _wait_until(lambda: len(processes) == 2)
        processes[1].exit_code = 9
        _wait_until(lambda: len(processes) == 3)
        snapshot = supervisor.snapshot()
        assert snapshot["automatic_restart_count"] == 2
        assert snapshot["consecutive_process_failures"] == 2
    finally:
        supervisor.request_pause()
        supervisor.close()


def test_waiting_for_token_never_restarts_until_token_is_submitted(
    tmp_path: Path,
) -> None:
    _write_status(
        tmp_path,
        {
            "state": "waiting_for_earthdata_token",
            "phase": "daymet_download",
            "current_city": "phoenix_az",
            "total": 12,
            "completed": 5,
            "events": [{"at": "now", "message": "token required"}],
        },
    )
    launches: list[tuple[dict[str, str], _FakeProcess]] = []

    def spawn(
        _command: tuple[str, ...],
        _cwd: Path,
        environment: dict[str, str],
    ) -> _FakeProcess:
        process = _FakeProcess()
        launches.append((dict(environment), process))
        return process

    supervisor = PortablePredictorSupervisor(
        tmp_path,
        spawn=spawn,  # type: ignore[arg-type]
        poll_seconds=0.003,
        initial_backoff_seconds=0.01,
        maximum_backoff_seconds=0.02,
        parent_environment={},
    )
    secret = "new-in-memory-secret"
    try:
        supervisor.begin_supervision()
        time.sleep(0.04)
        assert launches == []
        assert supervisor.snapshot()["token_required"] is True

        still_waiting = supervisor.start_or_continue("")
        assert still_waiting["state"] == "waiting_for_earthdata_token"
        time.sleep(0.03)
        assert launches == []

        started = supervisor.start_or_continue(secret)
        assert started["engine_process_running"] is True
        assert len(launches) == 1
        assert launches[0][0]["EARTHDATA_TOKEN"] == secret
        assert secret not in json.dumps(started)
        assert not any(
            secret in path.read_text(encoding="utf-8", errors="ignore")
            for path in tmp_path.rglob("*")
            if path.is_file()
        )

        launches[0][1].exit_code = 2
        _wait_until(lambda: not supervisor.snapshot()["engine_process_running"])
        time.sleep(0.05)
        assert len(launches) == 1
        assert supervisor.snapshot()["earthdata_token_in_memory"] is False
    finally:
        supervisor.close()


def test_status_normalization_shows_phase_city_eta_and_redacts_secrets() -> None:
    secret = "do-not-display-this"
    status = normalize_engine_status(
        {
            "state": "running",
            "stage": "static",
            "city_id": "chicago_il",
            "total_tasks": 9,
            "completed_tasks": 2,
            "active_tasks": [{"task_id": "static:chicago"}],
            "estimated_remaining_seconds": 123,
            "events": [
                {
                    "at": "now",
                    "message": (
                        "GET https://example.test/file?token=abc "
                        f"Authorization: Bearer {secret}"
                    ),
                }
            ],
        },
        secrets_to_redact=(secret,),
    )
    assert status["phase"] == "static"
    assert status["current_city"] == "chicago_il"
    assert status["total"] == 9
    assert status["completed"] == 2
    assert status["running"] == 1
    assert status["current"] == ["static:chicago"]
    assert status["eta_seconds"] == 123
    assert secret not in json.dumps(status)
    assert "token=abc" not in json.dumps(status)


def test_status_city_falls_back_to_current_task_mapping() -> None:
    status = normalize_engine_status(
        {
            "state": "running",
            "phase": "daymet",
            "current": {"task_id": "daymet:houston", "city_id": "houston_tx"},
            "total": 3,
            "completed": 1,
        }
    )
    assert status["current_city"] == "houston_tx"
    assert status["current"] == ["daymet:houston"]


def test_local_http_page_and_token_protected_controls(tmp_path: Path) -> None:
    _write_status(
        tmp_path,
        {"state": "waiting_for_earthdata_token", "total": 4, "completed": 1},
    )
    process = _FakeProcess()
    captured: list[dict[str, str]] = []

    def spawn(
        _command: tuple[str, ...],
        _cwd: Path,
        environment: dict[str, str],
    ) -> _FakeProcess:
        captured.append(dict(environment))
        return process

    supervisor = PortablePredictorSupervisor(
        tmp_path,
        spawn=spawn,  # type: ignore[arg-type]
        poll_seconds=0.01,
        parent_environment={},
    )
    server = create_server(host="127.0.0.1", port=0, supervisor=supervisor)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    secret = "http-only-secret"
    try:
        page = requests.get(base, timeout=5).text
        assert "四城市预测变量构建" in page
        assert 'type="password"' in page
        assert '.join("\\n")' in page
        assert '.join("\n")' not in page
        token = re.search(r'const control\s*=\s*"([^"]+)"', page).group(1)  # type: ignore[union-attr]
        assert requests.post(f"{base}/api/start", timeout=5).status_code == 403
        started = requests.post(
            f"{base}/api/start",
            headers={"X-Portable-Predictor-Control": token},
            json={"earthdata_token": secret},
            timeout=5,
        )
        assert started.status_code == 200
        assert secret not in started.text
        assert captured[0]["EARTHDATA_TOKEN"] == secret
        paused = requests.post(
            f"{base}/api/pause",
            headers={"X-Portable-Predictor-Control": token},
            json={},
            timeout=5,
        )
        assert paused.status_code == 200
        assert paused.json()["pause_requested"] is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        supervisor.close()


def test_dashboard_lock_and_loopback_binding(tmp_path: Path) -> None:
    path = tmp_path / "dashboard.lock"
    with DashboardProcessLock(path):
        with pytest.raises(DashboardAlreadyRunningError, match="already running"):
            with DashboardProcessLock(path):
                pass
    with DashboardProcessLock(path):
        assert path.exists()

    supervisor = PortablePredictorSupervisor(tmp_path, parent_environment={})
    try:
        with pytest.raises(ValueError, match="localhost"):
            create_server(host="0.0.0.0", port=8768, supervisor=supervisor)
    finally:
        supervisor.close()
