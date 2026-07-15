#!/usr/bin/env python3
"""Cross-platform atomic and locked state storage for the RPI core."""

from __future__ import annotations

import json
import os
import time
import base64
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

MAX_JSON_BYTES = 32 * 1024 * 1024
MAX_TRANSACTION_SNAPSHOT_BYTES = 16 * 1024 * 1024
MAX_TEXT_BYTES = 64 * 1024 * 1024

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
    lock_path = Path(f"{path}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if fcntl is not None:
        with lock_path.open("a+b") as handle:
            deadline = time.monotonic() + max(timeout_seconds, 0.0)
            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if timeout_seconds <= 0 or time.monotonic() >= deadline:
                        raise TimeoutError(f"Timed out waiting for lock: {lock_path}")
                    time.sleep(max(poll_seconds, 0.01))
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return
    if msvcrt is not None and os.name == "nt":
        with lock_path.open("a+b") as handle:
            handle.seek(0)
            handle.write(b"\0")
            handle.flush()
            deadline = time.monotonic() + max(timeout_seconds, 0.0)
            while True:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if timeout_seconds <= 0 or time.monotonic() >= deadline:
                        raise TimeoutError(f"Timed out waiting for lock: {lock_path}")
                    time.sleep(max(poll_seconds, 0.01))
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    yield


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        if path.is_symlink() or path.stat().st_size > MAX_JSON_BYTES:
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json_unlocked(path: Path, payload: Any) -> None:
    if path.is_symlink():
        raise RuntimeError(f"Refusing to replace symlinked state file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        if len(text.encode("utf-8")) > MAX_JSON_BYTES:
            raise ValueError(f"JSON state exceeds {MAX_JSON_BYTES} bytes: {path}")
        _write_durable_temp(tmp, text)
        os.replace(tmp, path)
        _fsync_directory(path.parent)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def write_text_unlocked(path: Path, text: str) -> None:
    if path.is_symlink():
        raise RuntimeError(f"Refusing to replace symlinked state file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
            raise ValueError(f"Text state exceeds {MAX_TEXT_BYTES} bytes: {path}")
        _write_durable_temp(tmp, text)
        os.replace(tmp, path)
        _fsync_directory(path.parent)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _write_durable_temp(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def write_json(path: Path, payload: Any) -> None:
    with exclusive_lock(path):
        write_json_unlocked(path, payload)


def write_text(path: Path, text: str) -> None:
    with exclusive_lock(path):
        write_text_unlocked(path, text)


def update_json(path: Path, default: Any, updater: Callable[[Any], Any]) -> Any:
    with exclusive_lock(path):
        current = read_json(path, default)
        updated = updater(current)
        write_json_unlocked(path, updated)
        return updated


def recover_transaction(journal_path: Path, allowed_root: Path | None = None) -> bool:
    """Roll back an interrupted multi-file transaction. Returns True when recovery ran."""
    if not journal_path.exists():
        return False
    journal = read_json(journal_path, {})
    if not isinstance(journal, dict):
        raise RuntimeError(f"Invalid transaction journal: {journal_path}")
    if journal.get("status") == "committed":
        journal_path.unlink(missing_ok=True)
        _fsync_directory(journal_path.parent)
        return False
    if journal.get("status") != "prepared" or not isinstance(journal.get("files"), list):
        raise RuntimeError(f"Unrecoverable transaction journal: {journal_path}")
    root = Path(str(journal.get("root", ""))).resolve()
    if not str(journal.get("root", "")):
        raise RuntimeError(f"Transaction journal has no recovery root: {journal_path}")
    if allowed_root is not None:
        boundary = allowed_root.resolve()
        if root != boundary:
            raise RuntimeError(f"Transaction journal root is outside the allowed workspace: {root}")
    for entry in journal["files"]:
        if not isinstance(entry, dict) or not entry.get("path"):
            raise RuntimeError(f"Invalid transaction entry in {journal_path}")
        relative = Path(str(entry["path"]))
        if relative.is_absolute() or ".." in relative.parts:
            raise RuntimeError(f"Unsafe transaction path in {journal_path}: {relative}")
        target = (root / relative).resolve()
        if target != root and root not in target.parents:
            raise RuntimeError(f"Transaction path escapes recovery root: {relative}")
        if entry.get("existed"):
            try:
                content = base64.b64decode(str(entry.get("content_base64", "")), validate=True)
            except (ValueError, TypeError) as exc:
                raise RuntimeError(f"Invalid transaction snapshot for {target}") from exc
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(f".{target.name}.{os.getpid()}.{time.time_ns()}.recovery.tmp")
            try:
                with tmp.open("wb") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp, target)
                _fsync_directory(target.parent)
            finally:
                tmp.unlink(missing_ok=True)
        else:
            target.unlink(missing_ok=True)
            _fsync_directory(target.parent)
    journal_path.unlink(missing_ok=True)
    _fsync_directory(journal_path.parent)
    return True


@contextmanager
def atomic_file_transaction(journal_path: Path, paths: Iterator[Path] | list[Path], root: Path) -> Iterator[None]:
    """Provide rollback recovery for a bounded set of atomically replaced files."""
    root = root.resolve()
    recover_transaction(journal_path, allowed_root=root)
    raw_paths = list(dict.fromkeys(Path(path).absolute() for path in paths))
    for path in raw_paths:
        if path.is_symlink():
            raise RuntimeError(f"Transaction target must not be a symlink: {path}")
    unique_paths = [path.resolve() for path in raw_paths]
    snapshots = []
    snapshot_bytes = 0
    for path in unique_paths:
        if path != root and root not in path.parents:
            raise ValueError(f"Transaction target escapes root {root}: {path}")
        existed = path.exists()
        if existed:
            size = path.stat().st_size
            snapshot_bytes += size
            if size > MAX_TRANSACTION_SNAPSHOT_BYTES or snapshot_bytes > MAX_TRANSACTION_SNAPSHOT_BYTES:
                raise ValueError(f"Transaction snapshot exceeds {MAX_TRANSACTION_SNAPSHOT_BYTES} bytes")
        snapshots.append(
            {
                "path": str(path.relative_to(root)),
                "existed": existed,
                "content_base64": base64.b64encode(path.read_bytes()).decode("ascii") if existed else "",
            }
        )
    write_json(journal_path, {"format_version": 2, "status": "prepared", "root": str(root), "created_at_ns": time.time_ns(), "files": snapshots})
    try:
        yield
    except BaseException:
        recover_transaction(journal_path, allowed_root=root)
        raise
    write_json(journal_path, {"format_version": 2, "status": "committed", "root": str(root), "created_at_ns": time.time_ns(), "files": snapshots})
    journal_path.unlink(missing_ok=True)
    _fsync_directory(journal_path.parent)
