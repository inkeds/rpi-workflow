#!/usr/bin/env python3
"""Cross-platform file lock helpers for JSON/JSONL writes."""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

try:
    import fcntl  # type: ignore
except Exception:  # pragma: no cover
    fcntl = None

try:
    import msvcrt  # type: ignore
except Exception:  # pragma: no cover
    msvcrt = None


@contextmanager
def exclusive_lock(path: Path, timeout_seconds: float = 10.0, poll_seconds: float = 0.05) -> Iterator[None]:
    """Acquire an exclusive lock using sidecar lock file (<path>.lock)."""
    lock_path = Path(f"{path}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    if fcntl is not None:
        with lock_path.open("a", encoding="utf-8") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        return

    if msvcrt is not None and os.name == "nt":
        with lock_path.open("a+b") as lock_handle:
            lock_handle.seek(0, os.SEEK_SET)
            lock_handle.write(b"\0")
            lock_handle.flush()

            deadline = time.monotonic() + max(timeout_seconds, 0.0)
            acquired = False
            while not acquired:
                try:
                    lock_handle.seek(0, os.SEEK_SET)
                    msvcrt.locking(lock_handle.fileno(), msvcrt.LK_NBLCK, 1)
                    acquired = True
                except OSError:
                    if timeout_seconds <= 0.0 or time.monotonic() >= deadline:
                        raise TimeoutError(f"Timed out waiting for lock: {lock_path}")
                    time.sleep(max(poll_seconds, 0.01))

            try:
                yield
            finally:
                try:
                    lock_handle.seek(0, os.SEEK_SET)
                    msvcrt.locking(lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
        return

    yield


def append_line_locked(path: Path, line: str) -> None:
    """Append a line to a file with cross-platform exclusive lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_lock(path):
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
