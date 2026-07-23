"""Cross-process single-instance lock for the final-model build entry point."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO


class FinalModelAlreadyRunning(RuntimeError):
    """Raised when another final-model build owns the process lock."""


def _lock(handle: BinaryIO) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:  # pragma: no cover - the project currently runs on Windows.
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(handle: BinaryIO) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:  # pragma: no cover - the project currently runs on Windows.
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def exclusive_final_model_process(lock_path: str | Path) -> Iterator[None]:
    """Hold an OS-released lock so force-stopped builds do not leave stale locks."""

    path = Path(lock_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
    try:
        _lock(handle)
    except OSError as error:
        handle.close()
        raise FinalModelAlreadyRunning(
            "Another build_final_models.py process already owns the final-model lock."
        ) from error
    try:
        yield
    finally:
        try:
            _unlock(handle)
        finally:
            handle.close()
