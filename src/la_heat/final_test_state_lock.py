"""Shared cross-process exclusion for final-test audit and authorization state."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Final

DEFAULT_FINAL_TEST_STATE_LOCK_PATH: Final = Path(
    "data/interim/final_test_2025/final_test_state.lock"
)


class FinalTestStateLockBusyError(RuntimeError):
    """Raised when another process is auditing or authorizing final-test state."""


class FinalTestStateLock:
    """Own one OS-released byte lock shared by audit and authorization."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._stream: Any = None

    def __enter__(self) -> FinalTestStateLock:
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
            raise FinalTestStateLockBusyError(
                "Another process owns the shared final-test state lock."
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
