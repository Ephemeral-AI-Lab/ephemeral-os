"""No-follow filesystem helpers for tool primitives."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path


def open_no_follow(path: str | Path, flags: int, mode: int = 0o666) -> int:
    """Open a path while refusing every symlink component.

    ``os.open(path, O_NOFOLLOW)`` only protects the final component. This helper
    walks from ``/`` with ``dir_fd`` and ``O_DIRECTORY | O_NOFOLLOW`` for each
    intermediate segment, preserving the daemon request-context policy.
    """
    parts = _absolute_parts(path)
    dir_fd = os.open("/", os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for segment in parts[:-1]:
            next_fd = os.open(
                segment,
                os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=dir_fd,
            )
            os.close(dir_fd)
            dir_fd = next_fd
        return os.open(parts[-1], flags | os.O_NOFOLLOW, mode, dir_fd=dir_fd)
    finally:
        os.close(dir_fd)


def read_bytes_no_follow(path: str | Path) -> bytes:
    fd = open_no_follow(path, os.O_RDONLY)
    with os.fdopen(fd, "rb") as handle:
        return handle.read()


def write_bytes_no_follow(
    path: str | Path,
    data: bytes,
    *,
    overwrite: bool = True,
) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT
    flags |= os.O_TRUNC if overwrite else os.O_EXCL
    fd = open_no_follow(target, flags)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)


def walk_dirs_no_follow(root: str | Path) -> Iterator[Path]:
    """Yield files under ``root`` without descending through symlink dirs."""
    for current_root, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [
            name
            for name in dirs
            if not (Path(current_root) / name).is_symlink()
        ]
        for name in files:
            path = Path(current_root) / name
            if not path.is_symlink():
                yield path


def _absolute_parts(path: str | Path) -> tuple[str, ...]:
    candidate = Path(path)
    if not candidate.is_absolute():
        raise ValueError(f"path must be absolute: {path!r}")
    parts = tuple(part for part in candidate.parts if part not in ("", "/"))
    if not parts:
        raise ValueError("path must not be filesystem root")
    if any(part in (".", "..") for part in parts):
        raise ValueError(f"path contains unsafe segment: {path!r}")
    return parts


__all__ = [
    "open_no_follow",
    "read_bytes_no_follow",
    "walk_dirs_no_follow",
    "write_bytes_no_follow",
]
