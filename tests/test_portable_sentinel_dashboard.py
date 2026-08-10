from __future__ import annotations

import json
import re
import sys
import threading
import time
from pathlib import Path

import requests

from la_heat.multicity.portable_sentinel_dashboard import (
    DEFAULT_PORT,
    PAUSE_FILENAME,
    RUNTIME_RELATIVE_DIRECTORY,
    STATUS_FILENAME,
    PortableSentinelSupervisor,
    build_engine_command,
    create_server,
    normalize_engine_status,
)


class _FakeProcess:
    _pid = 9000

    def __init__(self) -> None:
        type(self)._pid += 1
        self.pid = type(self)._pid
        self.exit_code: int | None = None

    def poll(self) -> int | None:
        return self.exit_code


def _wait_until(predicate: object, *, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if callable(predicate) and predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not reached")


def _runtime(root: Path) -> Path:
    return root / RUNTIME_RELATIVE_DIRECTORY


def _write_status(root: Path, payload: dict[str, object]) -> None:
    path = _runtime(root) / STATUS_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_command_uses_independent_download_and_acquisition_settings(
    tmp_path: Path,
) -> None:
    assert DEFAULT_PORT == 8769
    assert build_engine_command(tmp_path, download_threads=8, acquisition_concurrency=2) == (
        sys.executable,
        str((tmp_path / "scripts/build_portable_sentinel_features.py").resolve()),
        "--download-threads",
        "8",
        "--acquisition-concurrency",
        "2",
    )


def test_status_normalizes_city_progress_retries_and_current() -> None:
    status = normalize_engine_status(
        {
            "state": "running",
            "phase": "acquisitions",
            "total_tasks": 520,
            "completed_tasks": 31,
            "retry_count": 3,
            "current_task": {
                "city_id": "phoenix_az",
                "acquisition_key": "2025-07-14|32|T12SVD",
            },
            "estimated_remaining_seconds": 3601,
            "city_progress": {
                "phoenix_az": {"total": 116, "completed": 31, "failed": 1},
                "los_angeles_ca": {"total": 226, "completed": 0},
            },
        }
    )
    assert status["total"] == 520
    assert status["completed"] == 31
    assert status["retries"] == 3
    assert status["current_city"] == "phoenix_az"
    assert status["current"] == ["2025-07-14|32|T12SVD"]
    assert status["cities"][0]["city_id"] == "los_angeles_ca"
    assert status["cities"][1]["failed"] == 1


def test_start_pause_and_settings_are_persistent_and_cooperative(
    tmp_path: Path,
) -> None:
    launches: list[tuple[tuple[str, ...], _FakeProcess]] = []

    def spawn(
        command: tuple[str, ...],
        _cwd: Path,
        _environment: dict[str, str],
        _log_path: Path,
    ) -> _FakeProcess:
        process = _FakeProcess()
        launches.append((command, process))
        return process

    supervisor = PortableSentinelSupervisor(
        tmp_path,
        spawn=spawn,  # type: ignore[arg-type]
        poll_seconds=0.005,
        parent_environment={},
    )
    try:
        supervisor.begin_supervision()
        time.sleep(0.03)
        assert launches == []
        started = supervisor.start_or_continue(download_threads=8, acquisition_concurrency=1)
        assert started["engine_process_running"] is True
        assert launches[0][0][-4:] == (
            "--download-threads",
            "8",
            "--acquisition-concurrency",
            "1",
        )
        paused = supervisor.request_pause()
        assert paused["state"] == "pausing"
        assert (_runtime(tmp_path) / PAUSE_FILENAME).is_file()
        assert launches[0][1].exit_code is None
        launches[0][1].exit_code = 0
        _wait_until(lambda: supervisor.snapshot()["state"] == "paused")
        assert len(launches) == 1

        continued = supervisor.start_or_continue(download_threads=6, acquisition_concurrency=2)
        assert continued["engine_process_running"] is True
        assert launches[1][0][-4:] == (
            "--download-threads",
            "6",
            "--acquisition-concurrency",
            "2",
        )
    finally:
        supervisor.request_pause()
        supervisor.close()


def test_unexpected_exit_restarts_with_backoff_even_after_failed_status(
    tmp_path: Path,
) -> None:
    _write_status(
        tmp_path,
        {
            "state": "failed",
            "total": 520,
            "completed": 20,
            "failed": 1,
            "error": {"message": "temporary COG read failure", "retryable": True},
        },
    )
    processes: list[_FakeProcess] = []

    def spawn(*_args: object) -> _FakeProcess:
        process = _FakeProcess()
        processes.append(process)
        return process

    supervisor = PortableSentinelSupervisor(
        tmp_path,
        spawn=spawn,  # type: ignore[arg-type]
        poll_seconds=0.003,
        initial_backoff_seconds=0.01,
        maximum_backoff_seconds=0.02,
        parent_environment={},
    )
    try:
        supervisor.begin_supervision()
        supervisor.start_or_continue(download_threads=6, acquisition_concurrency=1)
        _wait_until(lambda: len(processes) == 1)
        processes[0].exit_code = 9
        _wait_until(lambda: len(processes) == 2)
        snapshot = supervisor.snapshot()
        assert snapshot["automatic_restart_count"] == 1
        assert snapshot["engine_process_running"] is True
    finally:
        supervisor.request_pause()
        supervisor.close()


def test_http_page_exposes_controls_and_protects_actions(tmp_path: Path) -> None:
    process = _FakeProcess()

    def spawn(*_args: object) -> _FakeProcess:
        return process

    supervisor = PortableSentinelSupervisor(
        tmp_path,
        spawn=spawn,  # type: ignore[arg-type]
        poll_seconds=0.01,
        parent_environment={},
    )
    server = create_server(host="127.0.0.1", port=0, supervisor=supervisor)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        response = requests.get(base, timeout=5)
        assert response.status_code == 200
        assert "四城 Sentinel-2 特征构建" in response.text
        assert "同时处理 acquisition" in response.text
        token = re.search(r'const control="([^"]+)"', response.text).group(1)  # type: ignore[union-attr]
        assert requests.post(f"{base}/api/start", json={}, timeout=5).status_code == 403
        started = requests.post(
            f"{base}/api/start",
            headers={"X-Sentinel-Control": token},
            json={"download_threads": 8, "acquisition_concurrency": 1},
            timeout=5,
        )
        assert started.status_code == 200
        assert started.json()["download_threads"] == 8
        paused = requests.post(
            f"{base}/api/pause",
            headers={"X-Sentinel-Control": token},
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
