"""External process supervisor for the local Sentinel-2 dashboard.

The watchdog deliberately knows nothing about acquisition caches or scientific
outputs. It starts the existing dashboard entry point, waits for that child to
exit, and retries only when the exit was not successful. The dashboard remains
the sole owner of its cache-tree lock and all checkpoint semantics.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Protocol


class WaitableProcess(Protocol):
    """Small process surface needed by the restart loop."""

    def wait(self) -> int:
        """Wait for the child and return its process exit code."""


SpawnProcess = Callable[[tuple[str, ...], Path], WaitableProcess]
Sleep = Callable[[float], None]
Emit = Callable[[str], None]


class WatchdogAlreadyRunningError(RuntimeError):
    """A second watchdog attempted to supervise the same dashboard."""


class WatchdogProcessLock:
    """OS-released, single-byte lock preventing duplicate watchdogs."""

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
        except (OSError, BlockingIOError) as exc:
            stream.close()
            raise WatchdogAlreadyRunningError(
                "Another Sentinel dashboard watchdog already holds the supervisor lock."
            ) from exc
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
) -> tuple[str, ...]:
    """Build the exact child command using the active Python interpreter."""

    if workers not in {1, 2}:
        raise ValueError("Dashboard workers must be 1 or 2.")
    if not 1 <= port <= 65_535:
        raise ValueError("Dashboard port must be between 1 and 65535.")
    root = Path(project_root).resolve()
    command = [
        sys.executable,
        str(root / "scripts" / "sentinel_dashboard.py"),
        "--workers",
        str(workers),
        "--host",
        host,
        "--port",
        str(port),
    ]
    if no_browser:
        command.append("--no-browser")
    return tuple(command)


def _spawn_dashboard(command: tuple[str, ...], cwd: Path) -> WaitableProcess:
    """Start one quiet dashboard child in an explicit working directory."""

    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    return subprocess.Popen(  # noqa: S603 - fixed local entry point, no shell
        command,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creation_flags,
    )


def _backoff_seconds(
    failure_count: int,
    *,
    initial_seconds: float,
    maximum_seconds: float,
) -> float:
    exponent = min(failure_count - 1, 62)
    return min(initial_seconds * (2**exponent), maximum_seconds)


def supervise_dashboard(
    project_root: str | Path,
    *,
    workers: int = 1,
    host: str = "127.0.0.1",
    port: int = 8765,
    no_browser: bool = False,
    initial_backoff_seconds: float = 2.0,
    maximum_backoff_seconds: float = 300.0,
    spawn: SpawnProcess = _spawn_dashboard,
    sleep: Sleep = time.sleep,
    emit: Emit = print,
) -> int:
    """Restart a failed dashboard child until one exits normally.

    Exception messages, child commands, paths, and URLs are never emitted. This
    function intentionally reports only the event type and its attempt count.
    """

    if initial_backoff_seconds <= 0:
        raise ValueError("Initial watchdog backoff must be positive.")
    if maximum_backoff_seconds < initial_backoff_seconds:
        raise ValueError("Maximum watchdog backoff must not be below the initial value.")

    root = Path(project_root).resolve()
    command = build_dashboard_command(
        root,
        workers=workers,
        host=host,
        port=port,
        no_browser=no_browser,
    )
    failure_count = 0
    launch_count = 0

    while True:
        launch_count += 1
        try:
            process = spawn(command, root)
        except Exception as exc:  # noqa: BLE001 - output is deliberately type-only
            failure_count += 1
            emit(f"event=spawn_exception type={type(exc).__name__} count={launch_count}")
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
    """Run one locked watchdog instance from the command line."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, choices=(1, 2), default=1)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--initial-backoff-seconds", type=float, default=2.0)
    parser.add_argument("--maximum-backoff-seconds", type=float, default=300.0)
    arguments = parser.parse_args(argv)

    project_root = Path(__file__).resolve().parents[2]
    lock_path = (
        project_root
        / "data"
        / "interim"
        / "sentinel_features"
        / "dashboard_watchdog.lock"
    )
    try:
        with WatchdogProcessLock(lock_path):
            return supervise_dashboard(
                project_root,
                workers=arguments.workers,
                host=arguments.host,
                port=arguments.port,
                no_browser=arguments.no_browser,
                initial_backoff_seconds=arguments.initial_backoff_seconds,
                maximum_backoff_seconds=arguments.maximum_backoff_seconds,
            )
    except WatchdogAlreadyRunningError as exc:
        print(f"event=watchdog_exception type={type(exc).__name__} count=1")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
