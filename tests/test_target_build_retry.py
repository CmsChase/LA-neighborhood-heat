from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import la_heat.target_build_retry as retry
from la_heat.target_build_retry import (
    CompletionMarker,
    TargetBuildAlreadyRunningError,
    TargetBuildProcessLock,
    build_target_command,
    inspect_build_progress,
    supervise_target_build,
)


def test_command_uses_active_python_module_and_absolute_paths(tmp_path: Path) -> None:
    command = build_target_command(
        tmp_path,
        config_path="configs/sensitivity.toml",
        output_directory="data/interim/sensitivity",
    )

    assert command == (
        sys.executable,
        "-m",
        "la_heat.target_builder",
        "--config",
        str((tmp_path / "configs/sensitivity.toml").resolve()),
        "--output-directory",
        str((tmp_path / "data/interim/sensitivity").resolve()),
    )


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"build_complete": False, "state": "building"}, (False, "build_incomplete")),
        ({"build_complete": True, "state": "partial_ready"}, (False, "invalid_state")),
        ({"build_complete": True, "state": "model_ready"}, (True, "complete")),
        ({"build_complete": True, "state": "complete_gate_failed"}, (True, "complete")),
    ],
)
def test_progress_requires_complete_flag_and_allowed_terminal_state(
    tmp_path: Path,
    payload: dict[str, object],
    expected: tuple[bool, str],
) -> None:
    path = tmp_path / "build_progress.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    marker = inspect_build_progress(path)

    assert (marker.complete, marker.status) == expected


def test_missing_and_malformed_progress_are_not_success(tmp_path: Path) -> None:
    path = tmp_path / "build_progress.json"
    assert inspect_build_progress(path).status == "missing"
    path.write_text("{not-json", encoding="utf-8")
    assert inspect_build_progress(path).status == "invalid_json"


def test_nonzero_exit_retries_then_requires_zero_exit_and_complete_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "output"
    output.mkdir()
    attempts = 0
    subprocess_calls: list[tuple[tuple[str, ...], Path, bool]] = []
    sleeps: list[float] = []
    events: list[str] = []

    def fake_subprocess_run(
        command: tuple[str, ...], *, cwd: Path, check: bool
    ) -> SimpleNamespace:
        nonlocal attempts
        attempts += 1
        subprocess_calls.append((command, cwd, check))
        if attempts == 2:
            (output / "build_progress.json").write_text(
                json.dumps({"build_complete": True, "state": "model_ready"}),
                encoding="utf-8",
            )
            return SimpleNamespace(returncode=0)
        return SimpleNamespace(returncode=9)

    monkeypatch.setattr(retry.subprocess, "run", fake_subprocess_run)

    result = supervise_target_build(
        tmp_path,
        config_path="config.toml",
        output_directory=output,
        max_attempts=3,
        retry_delay_seconds=7.5,
        run_child=retry._run_target_builder,
        sleep=sleeps.append,
        emit=events.append,
    )

    assert result == 0
    assert attempts == 2
    assert sleeps == [7.5]
    assert all(call[1:] == (tmp_path.resolve(), False) for call in subprocess_calls)
    assert "reason=nonzero_exit" in events[1]
    assert events[-1].startswith("event=target_build_complete attempt=2")


def test_zero_exit_with_incomplete_marker_is_retried(tmp_path: Path) -> None:
    markers = iter(
        [
            CompletionMarker(False, "missing"),
            CompletionMarker(False, "build_incomplete", "building"),
            CompletionMarker(True, "complete", "complete_gate_failed"),
        ]
    )
    calls = 0
    sleeps: list[float] = []
    events: list[str] = []

    def run_child(_command: tuple[str, ...], _cwd: Path) -> int:
        nonlocal calls
        calls += 1
        return 0

    result = supervise_target_build(
        tmp_path,
        config_path="config.toml",
        output_directory="output",
        max_attempts=2,
        retry_delay_seconds=0,
        run_child=run_child,
        sleep=sleeps.append,
        inspect_progress=lambda _path: next(markers),
        emit=events.append,
    )

    assert result == 0
    assert calls == 2
    assert sleeps == [0]
    assert "reason=build_incomplete" in events[1]
    assert "state=complete_gate_failed" in events[-1]


def test_existing_complete_marker_skips_child(tmp_path: Path) -> None:
    calls = 0
    events: list[str] = []

    def run_child(_command: tuple[str, ...], _cwd: Path) -> int:
        nonlocal calls
        calls += 1
        return 0

    result = supervise_target_build(
        tmp_path,
        config_path="config.toml",
        output_directory="output",
        max_attempts=2,
        retry_delay_seconds=1,
        run_child=run_child,
        inspect_progress=lambda _path: CompletionMarker(True, "complete", "model_ready"),
        emit=events.append,
    )

    assert result == 0
    assert calls == 0
    assert events == ["event=target_build_already_complete state=model_ready"]


def test_attempt_limit_returns_failure_without_extra_sleep(tmp_path: Path) -> None:
    sleeps: list[float] = []
    events: list[str] = []

    result = supervise_target_build(
        tmp_path,
        config_path="config.toml",
        output_directory="output",
        max_attempts=2,
        retry_delay_seconds=3,
        run_child=lambda _command, _cwd: 4,
        sleep=sleeps.append,
        inspect_progress=lambda _path: CompletionMarker(False, "missing"),
        emit=events.append,
    )

    assert result == 1
    assert sleeps == [3]
    assert events[-1] == (
        "event=target_build_exhausted attempts=2 last_reason=nonzero_exit"
    )


def test_process_lock_is_exclusive_and_released_by_os(tmp_path: Path) -> None:
    lock_path = tmp_path / retry.LOCK_FILENAME
    with TargetBuildProcessLock(lock_path):
        with pytest.raises(TargetBuildAlreadyRunningError, match="owns"):
            with TargetBuildProcessLock(lock_path):
                pass

    with TargetBuildProcessLock(lock_path):
        assert lock_path.exists()


@pytest.mark.parametrize(
    ("attempts", "delay", "message"),
    [(0, 1.0, "positive integer"), (1, -0.1, "non-negative")],
)
def test_invalid_retry_configuration_fails_before_child(
    tmp_path: Path, attempts: int, delay: float, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        supervise_target_build(
            tmp_path,
            config_path="config.toml",
            output_directory="output",
            max_attempts=attempts,
            retry_delay_seconds=delay,
            run_child=lambda _command, _cwd: pytest.fail("child must not run"),
        )
