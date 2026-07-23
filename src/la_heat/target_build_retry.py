"""Cache-preserving retry supervisor for the Landsat target builder.

The child process owns all target-cache and aggregate semantics.  This module
only launches the existing ``la_heat.target_builder`` entry point, retries a
failed or incompletely committed run, and prevents two supervisors from owning
the same output directory.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ALLOWED_COMPLETE_STATES = frozenset({"model_ready", "complete_gate_failed"})
LOCK_FILENAME = ".target_build_retry.lock"


class TargetBuildAlreadyRunningError(RuntimeError):
    """Raised when another retry supervisor owns the same output directory."""


class TargetBuildProcessLock:
    """OS-released lock preventing duplicate supervisors for one output tree."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._stream: Any = None

    def __enter__(self) -> TargetBuildProcessLock:
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
            raise TargetBuildAlreadyRunningError(
                "Another target-build retry supervisor owns this output directory."
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


@dataclass(frozen=True)
class CompletionMarker:
    """Validated completion state read from ``build_progress.json``."""

    complete: bool
    status: str
    state: str | None = None


RunChild = Callable[[tuple[str, ...], Path], int]
Sleep = Callable[[float], None]
Emit = Callable[[str], None]
InspectProgress = Callable[[Path], CompletionMarker]


def _resolve_from_root(project_root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (project_root / path).resolve()


def build_target_command(
    project_root: str | Path,
    *,
    config_path: str | Path,
    output_directory: str | Path,
) -> tuple[str, ...]:
    """Return the fixed, shell-free target-builder child command."""

    root = Path(project_root).resolve()
    config = _resolve_from_root(root, config_path)
    output = _resolve_from_root(root, output_directory)
    return (
        sys.executable,
        "-m",
        "la_heat.target_builder",
        "--config",
        str(config),
        "--output-directory",
        str(output),
    )


def inspect_build_progress(progress_path: str | Path) -> CompletionMarker:
    """Read and validate the only accepted target-build completion marker."""

    path = Path(progress_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return CompletionMarker(False, "missing")
    except json.JSONDecodeError:
        return CompletionMarker(False, "invalid_json")
    except OSError:
        return CompletionMarker(False, "unreadable")
    if not isinstance(payload, Mapping):
        return CompletionMarker(False, "invalid_schema")

    state = payload.get("state")
    normalized_state = state if isinstance(state, str) else None
    if payload.get("build_complete") is not True:
        return CompletionMarker(False, "build_incomplete", normalized_state)
    if normalized_state not in ALLOWED_COMPLETE_STATES:
        return CompletionMarker(False, "invalid_state", normalized_state)
    return CompletionMarker(True, "complete", normalized_state)


def _run_target_builder(command: tuple[str, ...], cwd: Path) -> int:
    """Run one child with the terminal's stdout and stderr inherited directly."""

    completed = subprocess.run(command, cwd=cwd, check=False)  # noqa: S603
    return int(completed.returncode)


def supervise_target_build(
    project_root: str | Path,
    *,
    config_path: str | Path,
    output_directory: str | Path,
    max_attempts: int,
    retry_delay_seconds: float,
    run_child: RunChild = _run_target_builder,
    sleep: Sleep = time.sleep,
    inspect_progress: InspectProgress = inspect_build_progress,
    emit: Emit = print,
) -> int:
    """Run and retry one cache-preserving target build until strictly complete."""

    if isinstance(max_attempts, bool) or max_attempts < 1:
        raise ValueError("max_attempts must be a positive integer.")
    if isinstance(retry_delay_seconds, bool) or retry_delay_seconds < 0:
        raise ValueError("retry_delay_seconds must be non-negative.")

    root = Path(project_root).resolve()
    output = _resolve_from_root(root, output_directory)
    progress_path = output / "build_progress.json"
    command = build_target_command(
        root,
        config_path=config_path,
        output_directory=output,
    )

    initial_marker = inspect_progress(progress_path)
    if initial_marker.complete:
        emit(f"event=target_build_already_complete state={initial_marker.state}")
        return 0

    for attempt in range(1, max_attempts + 1):
        emit(f"event=target_build_attempt_start attempt={attempt} max_attempts={max_attempts}")
        spawn_error_type: str | None = None
        try:
            exit_code = run_child(command, root)
        except OSError as error:
            exit_code = None
            spawn_error_type = type(error).__name__

        marker = inspect_progress(progress_path)
        if exit_code == 0 and marker.complete:
            emit(
                "event=target_build_complete "
                f"attempt={attempt} state={marker.state} progress_status={marker.status}"
            )
            return 0

        if spawn_error_type is not None:
            reason = "spawn_error"
            exit_value = spawn_error_type
        elif exit_code != 0:
            reason = "nonzero_exit"
            exit_value = str(exit_code)
        else:
            reason = marker.status
            exit_value = "0"
        emit(
            "event=target_build_attempt_failed "
            f"attempt={attempt} reason={reason} exit={exit_value} "
            f"progress_status={marker.status} state={marker.state}"
        )

        if attempt == max_attempts:
            emit(
                "event=target_build_exhausted "
                f"attempts={max_attempts} last_reason={reason}"
            )
            return 1
        emit(
            "event=target_build_retry "
            f"next_attempt={attempt + 1} delay_seconds={retry_delay_seconds:g}"
        )
        sleep(retry_delay_seconds)

    raise AssertionError("The bounded retry loop terminated unexpectedly.")


def run_target_build_with_retries(
    project_root: str | Path,
    *,
    config_path: str | Path,
    output_directory: str | Path,
    max_attempts: int,
    retry_delay_seconds: float,
) -> int:
    """Hold the per-output lock while supervising every retry attempt."""

    root = Path(project_root).resolve()
    output = _resolve_from_root(root, output_directory)
    with TargetBuildProcessLock(output / LOCK_FILENAME):
        return supervise_target_build(
            root,
            config_path=config_path,
            output_directory=output,
            max_attempts=max_attempts,
            retry_delay_seconds=retry_delay_seconds,
        )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the retry supervisor from a thin command-line entry point."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/research.toml")
    parser.add_argument("--output-directory", default="data/interim/targets")
    parser.add_argument("--max-attempts", type=int, default=10)
    parser.add_argument("--retry-delay-seconds", type=float, default=30.0)
    arguments = parser.parse_args(argv)
    project_root = Path(__file__).resolve().parents[2]
    try:
        return run_target_build_with_retries(
            project_root,
            config_path=arguments.config,
            output_directory=arguments.output_directory,
            max_attempts=arguments.max_attempts,
            retry_delay_seconds=arguments.retry_delay_seconds,
        )
    except TargetBuildAlreadyRunningError as error:
        print(f"event=target_build_wrapper_refused type={type(error).__name__}")
        return 2
    except ValueError as error:
        parser.error(str(error))


if __name__ == "__main__":
    raise SystemExit(main())
