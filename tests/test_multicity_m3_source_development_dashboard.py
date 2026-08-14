from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from pathlib import Path

import pytest
import requests

from la_heat.multicity.m3_source_development_dashboard import (
    CONTROL_FILENAME,
    DEFAULT_PORT,
    DEFAULT_WINDOW_SIZE,
    RUNTIME_RELATIVE_DIRECTORY,
    STATUS_FILENAME,
    M3SourceDevelopmentSupervisor,
    _configure_project_temp,
    build_worker_command,
    create_server,
    normalize_engine_status,
)


def _ready(_root: str | Path) -> dict[str, object]:
    return {"state": "ready_paused"}


class _FakeProcess:
    _pid = 12_000

    def __init__(self) -> None:
        type(self)._pid += 1
        self.pid = type(self)._pid
        self.exit_code: int | None = None

    def poll(self) -> int | None:
        return self.exit_code


def _runtime(root: Path) -> Path:
    return root / RUNTIME_RELATIVE_DIRECTORY


def _write_status(root: Path, payload: dict[str, object]) -> None:
    path = _runtime(root) / STATUS_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _wait_until(predicate: object, *, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if callable(predicate) and predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not reached")


def test_worker_command_freezes_office_compute_profile(tmp_path: Path) -> None:
    assert DEFAULT_PORT == 8772
    assert DEFAULT_WINDOW_SIZE == 512
    assert build_worker_command(
        tmp_path,
        phase="offline_qa_rebuild",
        download_workers=1,
    ) == (
        sys.executable,
        str((tmp_path / "scripts/run_m3_source_development_worker.py").resolve()),
        "--project-root",
        str(tmp_path.resolve()),
        "--phase",
        "offline_qa_rebuild",
        "--download-workers",
        "1",
        "--compute-workers",
        "1",
        "--window-size",
        "512",
        "--start",
    )
    with pytest.raises(ValueError, match="fixed at 1"):
        build_worker_command(tmp_path, compute_workers=2)
    with pytest.raises(ValueError, match="fixed at 512"):
        build_worker_command(tmp_path, window_size=256)


def test_dashboard_temp_is_forced_inside_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("TEMP", "TMP", "TMPDIR"):
        monkeypatch.setenv(name, r"C:\system-temp")
    expected = tmp_path / RUNTIME_RELATIVE_DIRECTORY / ".tmp"
    assert _configure_project_temp(tmp_path) == expected
    assert expected.is_dir()
    for name in ("TEMP", "TMP", "TMPDIR"):
        assert Path(os.environ[name]) == expected


def test_status_normalizes_network_and_three_progress_groups() -> None:
    status = normalize_engine_status(
        {
            "state": "waiting_for_network",
            "active_phase": "online_predownload",
            "phase": "download_asset",
            "network": {
                "state": "offline",
                "last_checked_at_utc": "2026-08-14T01:00:00Z",
                "message": "probe timed out",
            },
            "counts_by_kind": {
                "download_asset": {
                    "complete": 12,
                    "running": 2,
                    "pending": 66,
                    "quarantined": 0,
                    "total": 80,
                },
                "finalize_scene": {
                    "complete": 3,
                    "running": 0,
                    "pending": 7,
                    "quarantined": 0,
                    "total": 10,
                },
                "qa_overpass": {
                    "complete": 3,
                    "running": 0,
                    "pending": 151,
                    "quarantined": 0,
                    "total": 154,
                },
            },
            "download_bytes_completed": 1024,
            "download_bytes_total": 4096,
            "active_task_ids": ["asset-12", "asset-13"],
            "retry_count": 4,
            "estimated_remaining_seconds": 90,
        }
    )
    assert status["network"]["state"] == "offline"
    assert status["network"]["online"] is False
    assert status["download"] == {
        "completed": 15,
        "running": 2,
        "pending": 73,
        "failed": 0,
        "total": 90,
    }
    assert status["qa_rebuild"]["completed"] == 3
    assert status["qa_rebuild"]["total"] == 154
    assert status["loso"]["total"] == 0
    assert status["loso_locked"] is True
    assert status["worker_task_kind"] == "download_asset"
    assert status["current_tasks"] == ["asset-12", "asset-13"]
    assert status["eta_seconds"] == 90.0


def test_start_is_opt_in_pause_is_cooperative_and_control_persists(
    tmp_path: Path,
) -> None:
    launches: list[tuple[tuple[str, ...], dict[str, str], _FakeProcess]] = []

    def spawn(
        command: tuple[str, ...],
        _cwd: Path,
        environment: dict[str, str],
        _log_path: Path,
    ) -> _FakeProcess:
        process = _FakeProcess()
        launches.append((command, environment, process))
        return process

    supervisor = M3SourceDevelopmentSupervisor(
        tmp_path,
        spawn=spawn,  # type: ignore[arg-type]
        poll_seconds=0.005,
        parent_environment={},
        readiness_probe=_ready,
    )
    try:
        supervisor.begin_supervision()
        time.sleep(0.03)
        assert launches == []
        assert supervisor.snapshot()["dashboard_created_authorization"] is False

        started = supervisor.start_or_continue(
            phase="online_predownload", download_workers=2
        )
        assert started["worker_process_running"] is True
        assert launches[0][0][-9:] == (
            "--phase",
            "online_predownload",
            "--download-workers",
            "2",
            "--compute-workers",
            "1",
            "--window-size",
            "512",
            "--start",
        )
        for name in (
            "OMP_NUM_THREADS",
            "MKL_NUM_THREADS",
            "OPENBLAS_NUM_THREADS",
            "NUMEXPR_NUM_THREADS",
            "NUMEXPR_MAX_THREADS",
            "GDAL_NUM_THREADS",
        ):
            assert launches[0][1][name] == "1"

        paused = supervisor.request_pause()
        assert paused["state"] == "pausing"
        assert launches[0][2].exit_code is None
        control = json.loads(
            (_runtime(tmp_path) / CONTROL_FILENAME).read_text(encoding="utf-8")
        )
        assert control["desired_state"] == "paused"
        assert control["download_workers"] == 2
        assert not list(tmp_path.rglob("VALUES_OPENED.json"))
        assert not list(tmp_path.rglob("*AUTHORIZATION*.json"))

        launches[0][2].exit_code = 0
        _wait_until(lambda: supervisor.snapshot()["state"] == "paused")
        assert len(launches) == 1
    finally:
        supervisor.request_pause()
        supervisor.close()


def test_unexpected_exit_restarts_and_preserves_settings(tmp_path: Path) -> None:
    processes: list[_FakeProcess] = []

    def spawn(*_args: object) -> _FakeProcess:
        process = _FakeProcess()
        processes.append(process)
        return process

    supervisor = M3SourceDevelopmentSupervisor(
        tmp_path,
        spawn=spawn,  # type: ignore[arg-type]
        poll_seconds=0.003,
        initial_backoff_seconds=0.01,
        maximum_backoff_seconds=0.02,
        parent_environment={},
        readiness_probe=_ready,
    )
    try:
        supervisor.begin_supervision()
        supervisor.start_or_continue(phase="offline_qa_rebuild", download_workers=1)
        _wait_until(lambda: len(processes) == 1)
        processes[0].exit_code = 9
        _wait_until(lambda: len(processes) == 2)
        status = supervisor.snapshot()
        assert status["automatic_restart_count"] == 1
        assert status["selected_phase"] == "offline_qa_rebuild"
        assert status["compute_workers"] == 1
        assert status["window_size"] == 512
    finally:
        supervisor.request_pause()
        supervisor.close()


def test_http_page_exposes_protected_controls_and_progress(tmp_path: Path) -> None:
    _write_status(
        tmp_path,
        {
            "state": "paused",
            "phase": "online_predownload",
            "network": {"state": "online"},
            "download": {"completed": 2, "total": 10},
            "qa_rebuild": {"completed": 0, "total": 154},
            "loso": {"completed": 0, "total": 16},
        },
    )
    process = _FakeProcess()
    supervisor = M3SourceDevelopmentSupervisor(
        tmp_path,
        spawn=lambda *_args: process,
        parent_environment={},
        readiness_probe=_ready,
    )
    server = create_server(host="127.0.0.1", port=0, supervisor=supervisor)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        page = requests.get(base, timeout=5)
        assert page.status_code == 200
        assert "联网预下载" in page.text
        assert "离线QA重建" in page.text
        assert "待 QA 完成后另行授权；本页面不会启动" in page.text
        assert "512" in page.text
        token_match = re.search(r'const control="([^"]+)"', page.text)
        assert token_match is not None
        token = token_match.group(1)
        assert requests.post(f"{base}/api/start", json={}, timeout=5).status_code == 403
        started = requests.post(
            f"{base}/api/start",
            headers={"X-M3-Control": token},
            json={
                "phase": "online_predownload",
                "download_workers": 2,
                "compute_workers": 1,
                "window_size": 512,
            },
            timeout=5,
        )
        assert started.status_code == 200
        assert started.json()["download_workers"] == 2
        status = requests.get(f"{base}/api/status", timeout=5).json()
        assert status["download"]["completed"] == 2
        assert status["network"]["state"] == "online"
        paused = requests.post(
            f"{base}/api/pause",
            headers={"X-M3-Control": token},
            json={},
            timeout=5,
        )
        assert paused.status_code == 200
        assert paused.json()["desired_state"] == "paused"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        supervisor.close()


@pytest.mark.parametrize(
    ("readiness_state", "expected_message"),
    (
        ("waiting_for_source_acquisition_amendment", "正式的源数据扩展修订"),
        ("waiting_for_expanded_source_inventory", "固定窗口清单尚未完成"),
        ("waiting_for_source_qa_execution_authorization", "单独执行授权尚未签发"),
    ),
)
def test_start_gate_is_human_readable_and_never_enters_restart_loop(
    tmp_path: Path,
    readiness_state: str,
    expected_message: str,
) -> None:
    launches: list[_FakeProcess] = []
    supervisor = M3SourceDevelopmentSupervisor(
        tmp_path,
        spawn=lambda *_args: launches.append(_FakeProcess()) or launches[-1],
        poll_seconds=0.003,
        initial_backoff_seconds=0.005,
        readiness_probe=lambda _root: {"state": readiness_state},
        parent_environment={},
    )
    try:
        supervisor.begin_supervision()
        result = supervisor.start_or_continue(
            phase="online_predownload", download_workers=2
        )
        time.sleep(0.04)
        assert launches == []
        assert result["state"] == readiness_state
        assert result["desired_state"] == "paused"
        assert result["start_blocked"] is True
        assert expected_message in result["action_message"]
        assert supervisor.snapshot()["automatic_restart_count"] == 0
    finally:
        supervisor.close()


def test_normal_phase_completion_stops_instead_of_restarting(tmp_path: Path) -> None:
    processes: list[_FakeProcess] = []

    def spawn(*_args: object) -> _FakeProcess:
        process = _FakeProcess()
        processes.append(process)
        return process

    supervisor = M3SourceDevelopmentSupervisor(
        tmp_path,
        spawn=spawn,  # type: ignore[arg-type]
        poll_seconds=0.003,
        initial_backoff_seconds=0.005,
        readiness_probe=_ready,
        parent_environment={},
    )
    try:
        supervisor.begin_supervision()
        supervisor.start_or_continue(phase="online_predownload", download_workers=2)
        _wait_until(lambda: len(processes) == 1)
        _write_status(
            tmp_path,
            {
                "state": "paused",
                "active_phase": "online_predownload",
                "phase": "complete",
                "phase_complete": 5,
                "phase_total": 5,
            },
        )
        processes[0].exit_code = 0
        _wait_until(lambda: supervisor.snapshot()["state"] == "paused")
        time.sleep(0.03)
        assert len(processes) == 1
        assert supervisor.snapshot()["automatic_restart_count"] == 0
    finally:
        supervisor.close()
