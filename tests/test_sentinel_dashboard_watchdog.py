from __future__ import annotations

import sys
from pathlib import Path

import pytest

from la_heat.sentinel_dashboard_watchdog import (
    WatchdogAlreadyRunningError,
    WatchdogProcessLock,
    build_dashboard_command,
    supervise_dashboard,
)


class _FakeProcess:
    def __init__(self, exit_code: int) -> None:
        self._exit_code = exit_code

    def wait(self) -> int:
        return self._exit_code


def test_command_uses_current_python_explicit_root_and_passes_dashboard_options(
    tmp_path: Path,
) -> None:
    command = build_dashboard_command(
        tmp_path,
        workers=2,
        host="localhost",
        port=9123,
        no_browser=True,
    )

    assert command == (
        sys.executable,
        str(tmp_path.resolve() / "scripts" / "sentinel_dashboard.py"),
        "--workers",
        "2",
        "--host",
        "localhost",
        "--port",
        "9123",
        "--no-browser",
    )


def test_nonzero_exits_restart_with_capped_exponential_backoff(
    tmp_path: Path,
) -> None:
    exit_codes = iter([4, 9, 7, 0])
    launches: list[tuple[tuple[str, ...], Path]] = []
    sleeps: list[float] = []
    events: list[str] = []

    def spawn(command: tuple[str, ...], cwd: Path) -> _FakeProcess:
        launches.append((command, cwd))
        return _FakeProcess(next(exit_codes))

    result = supervise_dashboard(
        tmp_path,
        workers=2,
        host="127.0.0.1",
        port=8765,
        no_browser=True,
        initial_backoff_seconds=2,
        maximum_backoff_seconds=5,
        spawn=spawn,
        sleep=sleeps.append,
        emit=events.append,
    )

    assert result == 0
    assert len(launches) == 4
    assert all(cwd == tmp_path.resolve() for _, cwd in launches)
    assert all(command == launches[0][0] for command, _ in launches)
    assert sleeps == [2, 4, 5]
    assert events == [
        "event=child_exit type=nonzero count=1",
        "event=child_exit type=nonzero count=2",
        "event=child_exit type=nonzero count=3",
        "event=child_exit type=zero count=4",
    ]


def test_zero_exit_stops_without_sleep_or_restart(tmp_path: Path) -> None:
    calls = 0
    sleeps: list[float] = []

    def spawn(_command: tuple[str, ...], _cwd: Path) -> _FakeProcess:
        nonlocal calls
        calls += 1
        return _FakeProcess(0)

    assert (
        supervise_dashboard(
            tmp_path,
            spawn=spawn,
            sleep=sleeps.append,
            emit=lambda _message: None,
        )
        == 0
    )
    assert calls == 1
    assert sleeps == []


def test_spawn_exception_is_type_only_sanitized_and_retried(tmp_path: Path) -> None:
    calls = 0
    events: list[str] = []
    sleeps: list[float] = []

    def spawn(_command: tuple[str, ...], _cwd: Path) -> _FakeProcess:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("https://example.test/asset?sig=must-not-leak")
        return _FakeProcess(0)

    result = supervise_dashboard(
        tmp_path,
        spawn=spawn,
        sleep=sleeps.append,
        emit=events.append,
    )

    assert result == 0
    assert sleeps == [2]
    assert events[0] == "event=spawn_exception type=RuntimeError count=1"
    assert all("http" not in event and "sig=" not in event for event in events)


def test_watchdog_process_lock_is_exclusive_and_os_released(tmp_path: Path) -> None:
    lock_path = tmp_path / "watchdog.lock"
    with WatchdogProcessLock(lock_path):
        with pytest.raises(WatchdogAlreadyRunningError, match="already holds"):
            with WatchdogProcessLock(lock_path):
                pass

    with WatchdogProcessLock(lock_path):
        assert lock_path.exists()


@pytest.mark.parametrize(
    ("initial", "maximum", "message"),
    [(0.0, 10.0, "positive"), (10.0, 5.0, "below")],
)
def test_invalid_backoff_configuration_fails_before_spawn(
    tmp_path: Path,
    initial: float,
    maximum: float,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        supervise_dashboard(
            tmp_path,
            initial_backoff_seconds=initial,
            maximum_backoff_seconds=maximum,
            spawn=lambda _command, _cwd: _FakeProcess(0),
        )
