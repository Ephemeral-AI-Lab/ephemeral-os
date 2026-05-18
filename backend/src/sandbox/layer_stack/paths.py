"""Private filesystem/path helpers for layer-stack storage components."""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from pathlib import Path, PurePosixPath

# Per-snapshot scratch directories live at
# ``<storage_root>/runtime/<TRANSIENT_LOWERDIR_DIR>/<request-id>/lower``.
# Shared between the layer_stack snapshot path (which creates them) and the
# execution orchestrator cleanup path (which validates and removes them).
TRANSIENT_LOWERDIR_DIR = "transient-lowerdirs"


def join_layer_path(root: Path, rel: str) -> Path:
    if not rel:
        return root
    return root.joinpath(*PurePosixPath(rel).parts)


def remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)


def resolve_storage_path(storage_root: Path, path: str) -> Path:
    if "\0" in path:
        raise ValueError(f"layer path must not contain NUL bytes: {path!r}")
    candidate = Path(path)
    if candidate.is_absolute():
        raise ValueError(f"layer path must be relative: {path}")
    joined = storage_root / candidate
    resolved = joined.resolve(strict=False)
    storage_resolved = storage_root.resolve(strict=False)
    if not resolved.is_relative_to(storage_resolved):
        raise ValueError(
            f"layer path escapes storage_root: {path!r} -> {resolved}"
        )
    return joined


def fsync_path(path: Path) -> None:
    """fsync a regular file or directory by path."""
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_bytes_fsynced(path: Path, data: bytes) -> None:
    """Write *data* to *path* and fsync the file before returning.

    Caller is responsible for fsyncing the parent directory after the
    final ``os.replace`` (for replace-style atomic writes) or directly
    (for in-place writes that establish a new file).
    """
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


def replace_via_tmp_fsynced(target: Path, data: bytes) -> None:
    """Atomically install *data* at *target* with a tmp+rename+fsync dance."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.tmp")
    write_bytes_fsynced(tmp, data)
    os.replace(tmp, target)
    fsync_path(target.parent)


def relative_symlink_target_escapes(target: str) -> bool:
    """Return True if a relative symlink target walks out of its directory."""
    depth = 0
    for part in target.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if depth == 0:
                return True
            depth -= 1
        else:
            depth += 1
    return False


def allocate_unique_layer_paths(
    *,
    storage_root: Path,
    layers_dir: str,
    staging_dir: str,
    next_version: int,
    id_factory: Callable[[int], str],
    attempts: int = 100,
) -> tuple[str, Path, Path]:
    for _ in range(attempts):
        layer_id = id_factory(next_version)
        layer_dir = storage_root / layers_dir / layer_id
        pending_staging_dir = storage_root / staging_dir / f"{layer_id}.staging"
        if not layer_dir.exists() and not pending_staging_dir.exists():
            return layer_id, pending_staging_dir, layer_dir
    raise RuntimeError("could not allocate a unique layer id")


__all__ = [
    "TRANSIENT_LOWERDIR_DIR",
    "allocate_unique_layer_paths",
    "fsync_path",
    "join_layer_path",
    "relative_symlink_target_escapes",
    "remove_path",
    "replace_via_tmp_fsynced",
    "resolve_storage_path",
    "write_bytes_fsynced",
]
