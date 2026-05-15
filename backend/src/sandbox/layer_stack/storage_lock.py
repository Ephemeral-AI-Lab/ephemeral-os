"""Process-local advisory writer locks for layer-stack storage roots."""

from __future__ import annotations

import logging
import os
import threading
import weakref
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_STORAGE_WRITER_LOCK_FILE = ".storage-writer.lock"
_STORAGE_WRITER_LOCKS: dict[str, _StorageWriterLock] = {}
_STORAGE_WRITER_LOCKS_LOCK = threading.Lock()

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows-only fallback.
    fcntl = None  # type: ignore[assignment]


@dataclass
class _StorageWriterLock:
    fd: int
    refcount: int


class StorageWriterLockLease:
    def __init__(self, key: str, fd: int) -> None:
        self.fd = fd
        # weakref.finalize avoids the __del__ trap of running during
        # interpreter teardown after module globals (the dict and its
        # lock) have been cleared.
        self._finalizer = weakref.finalize(self, _release_storage_lock, key)

    def close(self) -> None:
        self._finalizer()


def _release_storage_lock(key: str) -> None:
    with _STORAGE_WRITER_LOCKS_LOCK:
        record = _STORAGE_WRITER_LOCKS.get(key)
        if record is None:
            return
        record.refcount -= 1
        if record.refcount > 0:
            return
        _STORAGE_WRITER_LOCKS.pop(key, None)
        if fcntl is not None:
            fcntl.flock(record.fd, fcntl.LOCK_UN)
        os.close(record.fd)


def acquire_storage_writer_lock(storage_root: Path) -> StorageWriterLockLease | None:
    """Hold a process-wide advisory writer lock for this storage root."""
    if fcntl is None:
        logger.warning(
            "layer-stack storage writer lock unavailable; fcntl is missing",
        )
        return None
    key = str(storage_root.resolve())
    with _STORAGE_WRITER_LOCKS_LOCK:
        record = _STORAGE_WRITER_LOCKS.get(key)
        if record is not None:
            record.refcount += 1
            return StorageWriterLockLease(key, record.fd)

        lock_path = storage_root / _STORAGE_WRITER_LOCK_FILE
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(fd)
            raise RuntimeError(
                "layer-stack storage root is already owned by another process: "
                f"{storage_root}"
            ) from exc
        _STORAGE_WRITER_LOCKS[key] = _StorageWriterLock(fd=fd, refcount=1)
        return StorageWriterLockLease(key, fd)


__all__ = ["StorageWriterLockLease", "acquire_storage_writer_lock"]
