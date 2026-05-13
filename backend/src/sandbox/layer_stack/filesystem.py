"""Filesystem helpers shared by layer-stack storage components."""

from __future__ import annotations

import shutil
from pathlib import Path, PurePosixPath


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
    if resolved != storage_resolved and not resolved.is_relative_to(
        storage_resolved
    ):
        raise ValueError(
            f"layer path escapes storage_root: {path!r} -> {resolved}"
        )
    return joined


__all__ = ["join_layer_path", "remove_path", "resolve_storage_path"]
