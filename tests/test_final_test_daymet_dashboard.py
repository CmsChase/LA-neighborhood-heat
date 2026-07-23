from __future__ import annotations

import json
import re
import sys
import threading
import time
from pathlib import Path

import pytest
import requests

from la_heat.final_test_daymet_dashboard import (
    DaymetDashboardAlreadyRunningError,
    DaymetDashboardLock,
    FinalTestDaymetSupervisor,
    build_engine_command,
    create_server,
    read_download_progress,
)


class _FakeProcess:
    _next_pid = 500

    def __init__(self) -> None:
        type(self)._next_pid += 1
        self.pid = type(self)._next_pid
        self.exit_code: int | None = None
        self.terminate_calls = 0
        self.kill_calls = 0

    def poll(self) -> int | None:
        return self.exit_code

    def terminate(self) -> None:
        self.terminate_calls += 1

    def kill(self) -> None:
        self.kill_calls += 1
        self.exit_code = -9


def _wait_until(predicate: object, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if callable(predicate) and predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not reached")


def test_engine_command_is_exact_and_contains_no_token(tmp_path: Path) -> None:
    command = build_engine_command(tmp_path)
    assert command == (
        sys.executable,
        str(
            (
                tmp_path.resolve()
                / "scripts"
                / "stage_final_test_daymet_grid.py"
            ).resolve()
        ),
        "--config",
        "configs/research.toml",
        "--download-subsets",
    )
    assert all("token" not in value.lower() for value in command)


def test_new_session_is_paused_and_never_auto_starts(tmp_path: Path) -> None:
    launches = 0

    def spawn(*_args: object) -> _FakeProcess:
        nonlocal launches
        launches += 1
        return _FakeProcess()

    supervisor = FinalTestDaymetSupervisor(
        tmp_path,
        spawn=spawn,  # type: ignore[arg-type]
        poll_seconds=0.01,
    )
    try:
        assert supervisor.snapshot()["state"] == "paused"
        time.sleep(0.03)
        assert launches == 0
    finally:
        supervisor.close()


def test_token_exists_only_in_transient_child_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "secret-Earthdata-value"
    captured_command: tuple[str, ...] | None = None
    captured_environment: dict[str, str] | None = None
    process = _FakeProcess()
    monkeypatch.setenv("NASA_EARTHDATA_TOKEN", "old-secret")
    monkeypatch.setenv("EDL_TOKEN", "other-secret")

    def spawn(
        command: tuple[str, ...],
        _cwd: Path,
        environment: dict[str, str],
    ) -> _FakeProcess:
        nonlocal captured_command, captured_environment
        captured_command = command
        captured_environment = dict(environment)
        return process

    supervisor = FinalTestDaymetSupervisor(
        tmp_path,
        spawn=spawn,  # type: ignore[arg-type]
        poll_seconds=0.01,
    )
    try:
        snapshot = supervisor.start_or_resume(secret)
        assert captured_environment is not None
        assert captured_environment["EARTHDATA_TOKEN"] == secret
        assert "NASA_EARTHDATA_TOKEN" not in captured_environment
        assert "EDL_TOKEN" not in captured_environment
        assert captured_command is not None
        assert secret not in " ".join(captured_command)
        assert secret not in json.dumps(snapshot)
        assert not any(
            secret in path.read_text(encoding="utf-8", errors="ignore")
            for path in tmp_path.rglob("*")
            if path.is_file()
        )
    finally:
        supervisor.close()


def test_progress_reads_atomic_manifest_and_stays_frozen_at_six(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "manifests" / "final_test_2025" / "daymet_grid"
    directory.mkdir(parents=True)
    (directory / "DAYMET_GRID.json").write_text(
        json.dumps(
            {
                "state": "subsets_partial",
                "expected_subset_count": 6,
                "completed_subset_count": 2,
            }
        ),
        encoding="utf-8",
    )
    (directory / "subset_downloads.csv").write_text(
        "variable,year\nprcp,2025\ntmax,2025\ntmin,2025\n",
        encoding="utf-8",
    )
    progress = read_download_progress(tmp_path)
    assert progress["completed"] == 3
    assert progress["total"] == 6
    assert progress["progress_fraction"] == 0.5


def test_pause_terminates_only_owned_child_and_resume_is_new_launch(
    tmp_path: Path,
) -> None:
    processes: list[_FakeProcess] = []

    def spawn(
        _command: tuple[str, ...],
        _cwd: Path,
        _environment: dict[str, str],
    ) -> _FakeProcess:
        process = _FakeProcess()
        processes.append(process)
        return process

    supervisor = FinalTestDaymetSupervisor(
        tmp_path,
        spawn=spawn,  # type: ignore[arg-type]
        poll_seconds=0.005,
        terminate_grace_seconds=0.02,
    )
    try:
        supervisor.start_or_resume("first-secret")
        paused = supervisor.request_pause()
        assert paused["state"] == "pausing"
        assert processes[0].terminate_calls == 1
        _wait_until(lambda: processes[0].kill_calls == 1)
        _wait_until(lambda: supervisor.snapshot()["state"] == "paused")
        supervisor.start_or_resume("second-secret")
        assert len(processes) == 2
        assert processes[1].terminate_calls == 0
    finally:
        supervisor.close()


def test_lock_is_exclusive_and_os_released(tmp_path: Path) -> None:
    path = tmp_path / "daymet.lock"
    with DaymetDashboardLock(path):
        with pytest.raises(
            DaymetDashboardAlreadyRunningError,
            match="already running",
        ):
            with DaymetDashboardLock(path):
                pass
    with DaymetDashboardLock(path):
        assert path.exists()


def test_http_password_is_not_echoed_and_control_requires_token(
    tmp_path: Path,
) -> None:
    process = _FakeProcess()
    supervisor = FinalTestDaymetSupervisor(
        tmp_path,
        spawn=lambda *_args: process,  # type: ignore[arg-type]
        poll_seconds=0.01,
    )
    server = create_server(host="127.0.0.1", port=0, supervisor=supervisor)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    secret = "http-secret-value"
    try:
        html = requests.get(base, timeout=5).text
        assert 'type="password"' in html
        assert "localStorage" not in html
        control = re.search(r'const control="([^"]+)"', html).group(1)  # type: ignore[union-attr]
        assert requests.post(f"{base}/api/start", timeout=5).status_code == 403
        response = requests.post(
            f"{base}/api/start",
            headers={"X-Daymet-Control": control},
            json={"earthdata_token": secret},
            timeout=5,
        )
        assert response.status_code == 200
        assert secret not in response.text
        status = requests.get(f"{base}/api/status", timeout=5)
        assert secret not in status.text
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        supervisor.close()


def test_server_rejects_non_loopback_binding(tmp_path: Path) -> None:
    supervisor = FinalTestDaymetSupervisor(tmp_path)
    try:
        with pytest.raises(ValueError, match="localhost"):
            create_server(host="0.0.0.0", port=8767, supervisor=supervisor)
    finally:
        supervisor.close()
