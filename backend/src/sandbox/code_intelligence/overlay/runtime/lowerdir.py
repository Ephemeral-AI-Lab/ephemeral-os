"""Lowerdir base-read helpers used inside the overlay namespace."""

from __future__ import annotations

import os
from collections.abc import Callable


def lowerdir_base_factory(*, lower_root: str) -> Callable[[str], bytes | None]:
    """Return a callable that reads command-start base bytes from ``lower_root``."""

    lower_root_abs = os.path.abspath(lower_root)

    def _read(rel: str) -> bytes | None:
        path = _safe_lower_path(lower_root_abs, rel)
        try:
            os.lstat(path)
        except (FileNotFoundError, NotADirectoryError):
            return None
        if os.path.islink(path):
            return os.readlink(path).encode("utf-8")
        if not os.path.isfile(path):
            return None
        with open(path, "rb") as fh:
            return fh.read()

    return _read


def _safe_lower_path(lower_root_abs: str, rel: str) -> str:
    if os.path.isabs(rel):
        raise RuntimeError(f"absolute overlay path is not allowed: {rel!r}")
    norm = os.path.normpath(rel.replace("\\", "/"))
    if norm in ("", ".") or norm.startswith("../"):
        raise RuntimeError(f"overlay path escapes lowerdir: {rel!r}")
    path = os.path.abspath(os.path.join(lower_root_abs, norm))
    if os.path.commonpath([lower_root_abs, path]) != lower_root_abs:
        raise RuntimeError(f"overlay path escapes lowerdir: {rel!r}")
    return path


__all__ = ["lowerdir_base_factory"]
