"""External watchdog that restarts the local grouped-model dashboard server."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Protocol

from la_heat.model_dashboard import DEFAULT_RUN_DIRECTORY, _backoff_seconds


class WaitableProcess(Protocol):
    """Small child-process interface needed by the watchdog."""

    def wait(self) -> int:
        """Wait for the child and return its exit code."""


SpawnDashboard = Callable[[tuple[str, ...], Path], WaitableProcess]
Sleep = Callable[[float], None]
Emit = Callable[[str], None]


class WatchdogAlreadyRunningError(RuntimeError):
    """Raised when a second watchdog attempts to own the same server."""


class WatchdogProcessLock:
    """OS-released lock preventing duplicate model-dashboard watchdogs."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._stream: Any = None

    def __enter__(self) -> WatchdogProcessLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+b")
        if self.path.stat().st_size == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as error:
            stream.close()
            raise WatchdogAlreadyRunningError(
                "Another model dashboard watchdog owns the supervisor lock."
            ) from error
        self._stream = stream
        return self

    def __exit__(self, *_args: object) -> None:
        stream = self._stream
        if stream is None:
            return
        try:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()
            self._stream = None


def build_dashboard_command(
    project_root: str | Path,
    *,
    workers: int,
    host: str,
    port: int,
    no_browser: bool,
    run_id: str | None = None,
    run_directory: str | Path = DEFAULT_RUN_DIRECTORY,
    coordinator_script: str | Path = "scripts/run_grouped_models.py",
) -> tuple[str, ...]:
    """Build the exact local dashboard argv without invoking a shell."""

    if isinstance(workers, bool) or not 1 <= int(workers) <= 8:
        raise ValueError("workers must be between 1 and 8.")
    if not 1 <= int(port) <= 65_535:
        raise ValueError("port must be between 1 and 65535.")
    root = Path(project_root).resolve()
    command = [
        sys.executable,
        str(root / "scripts" / "model_dashboard.py"),
        "--workers",
        str(int(workers)),
        "--host",
        host,
        "--port",
        str(int(port)),
        "--run-directory",
        str(run_directory),
        "--coordinator-script",
        str(coordinator_script),
    ]
    if run_id is not None:
        command.extend(("--run-id", run_id))
    if no_browser:
        command.append("--no-browser")
    return tuple(command)


def _spawn_dashboard(command: tuple[str, ...], cwd: Path) -> WaitableProcess:
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    return subprocess.Popen(  # noqa: S603 - fixed local argv, shell disabled
        command,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creation_flags,
    )


def supervise_dashboard(
    project_root: str | Path,
    *,
    workers: int = 4,
    host: str = "127.0.0.1",
    port: int = 8766,
    no_browser: bool = False,
    run_id: str | None = None,
    run_directory: str | Path = DEFAULT_RUN_DIRECTORY,
    coordinator_script: str | Path = "scripts/run_grouped_models.py",
    initial_backoff_seconds: float = 2.0,
    maximum_backoff_seconds: float = 300.0,
    spawn: SpawnDashboard = _spawn_dashboard,
    sleep: Sleep = time.sleep,
    emit: Emit = print,
) -> int:
    """Restart failed dashboard children with bounded exponential backoff."""

    if initial_backoff_seconds <= 0:
        raise ValueError("initial_backoff_seconds must be positive.")
    if maximum_backoff_seconds < initial_backoff_seconds:
        raise ValueError("maximum_backoff_seconds cannot be below the initial value.")
    root = Path(project_root).resolve()
    command = build_dashboard_command(
        root,
        workers=workers,
        host=host,
        port=port,
        no_browser=no_browser,
        run_id=run_id,
        run_directory=run_directory,
        coordinator_script=coordinator_script,
    )
    failure_count = 0
    launch_count = 0
    while True:
        launch_count += 1
        try:
            process = spawn(command, root)
        except Exception as error:  # noqa: BLE001 - output remains type-only
            failure_count += 1
            emit(f"event=spawn_exception type={type(error).__name__} count={launch_count}")
        else:
            exit_code = process.wait()
            if exit_code == 0:
                emit(f"event=child_exit type=zero count={launch_count}")
                return 0
            failure_count += 1
            emit(f"event=child_exit type=nonzero count={launch_count}")
        sleep(
            _backoff_seconds(
                failure_count,
                initial_seconds=initial_backoff_seconds,
                maximum_seconds=maximum_backoff_seconds,
            )
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, choices=range(1, 9), default=4)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--run-id")
    parser.add_argument("--run-directory", default=str(DEFAULT_RUN_DIRECTORY))
    parser.add_argument("--coordinator-script", default="scripts/run_grouped_models.py")
    parser.add_argument("--initial-backoff-seconds", type=float, default=2.0)
    parser.add_argument("--maximum-backoff-seconds", type=float, default=300.0)
    arguments = parser.parse_args(argv)

    project_root = Path(__file__).resolve().parents[2]
    run_directory = Path(arguments.run_directory)
    if not run_directory.is_absolute():
        run_directory = project_root / run_directory
    try:
        with WatchdogProcessLock(run_directory.resolve() / "dashboard_watchdog.lock"):
            return supervise_dashboard(
                project_root,
                workers=arguments.workers,
                host=arguments.host,
                port=arguments.port,
                no_browser=arguments.no_browser,
                run_id=arguments.run_id,
                run_directory=arguments.run_directory,
                coordinator_script=arguments.coordinator_script,
                initial_backoff_seconds=arguments.initial_backoff_seconds,
                maximum_backoff_seconds=arguments.maximum_backoff_seconds,
            )
    except WatchdogAlreadyRunningError as error:
        print(f"event=watchdog_exception type={type(error).__name__} count=1")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
