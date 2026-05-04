"""Upperdir capture helpers for the sandbox-side overlay runtime."""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator

from .mounts import _NS_LOWER
from .types import UpperChange, UpperChangeKind, UpperEntry


def walk_upperdir(upper_root: str) -> Iterator[UpperEntry]:
    """Yield one upperdir entry per captured overlay mutation."""
    upper_root = upper_root.rstrip("/")
    if not os.path.isdir(upper_root):
        return
    for dirpath, dirnames, filenames in os.walk(
        upper_root, topdown=True, followlinks=False
    ):
        rel_dir = os.path.relpath(dirpath, upper_root)
        rel_dir = "" if rel_dir == "." else rel_dir

        if rel_dir:
            full = os.path.join(upper_root, rel_dir)
            try:
                st = os.lstat(full)
            except FileNotFoundError:
                pass
            else:
                xattrs = _read_xattrs(full)
                if is_opaque_dir(st, xattrs):
                    yield UpperEntry(
                        rel=rel_dir, st=st, xattrs=xattrs, upper_path=full
                    )

        for name in filenames:
            rel = os.path.join(rel_dir, name) if rel_dir else name
            full = os.path.join(dirpath, name)
            try:
                st = os.lstat(full)
            except FileNotFoundError:
                continue
            yield UpperEntry(
                rel=rel,
                st=st,
                xattrs=_read_xattrs(full),
                upper_path=full,
            )

        dirnames.sort()


def build_upper_change(entry: UpperEntry) -> UpperChange:
    kind = _entry_kind(entry)
    base_bytes = _read_base_bytes(entry.rel)
    upper_bytes: bytes | None
    if kind == "regular":
        upper_bytes = _read_file_bytes(entry.upper_path)
    elif kind == "symlink":
        upper_bytes = os.readlink(entry.upper_path).encode("utf-8")
    else:
        upper_bytes = None
    return UpperChange(
        rel=entry.rel,
        kind=kind,
        base_bytes=base_bytes,
        upper_bytes=upper_bytes,
        base_existed=base_bytes is not None,
    )


def is_whiteout(st: os.stat_result, xattrs: dict[bytes, bytes]) -> bool:
    rdev = getattr(st, "st_rdev", None)
    if stat.S_ISCHR(st.st_mode) and rdev in (0, None):
        return True
    return stat.S_ISREG(st.st_mode) and st.st_size == 0 and (
        b"user.overlay.whiteout" in xattrs
    )


def is_opaque_dir(st: os.stat_result, xattrs: dict[bytes, bytes]) -> bool:
    if not stat.S_ISDIR(st.st_mode):
        return False
    return (
        xattrs.get(b"trusted.overlay.opaque") == b"y"
        or xattrs.get(b"user.overlay.opaque") == b"y"
    )


def _entry_kind(entry: UpperEntry) -> UpperChangeKind:
    if is_whiteout(entry.st, entry.xattrs):
        return "whiteout"
    if stat.S_ISLNK(entry.st.st_mode):
        return "symlink"
    if is_opaque_dir(entry.st, entry.xattrs):
        return "opaque_dir"
    return "regular"


def _read_base_bytes(rel: str) -> bytes | None:
    path = _safe_lower_path(_NS_LOWER, rel)
    try:
        os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return None
    if os.path.islink(path):
        return os.readlink(path).encode("utf-8")
    if not os.path.isfile(path):
        return None
    return _read_file_bytes(path)


def _safe_lower_path(lower_root: str, rel: str) -> str:
    lower_root_abs = os.path.abspath(lower_root)
    if os.path.isabs(rel):
        raise RuntimeError(f"absolute overlay path is not allowed: {rel!r}")
    norm = os.path.normpath(rel.replace("\\", "/"))
    if norm in ("", ".") or norm.startswith("../"):
        raise RuntimeError(f"overlay path escapes lowerdir: {rel!r}")
    path = os.path.abspath(os.path.join(lower_root_abs, norm))
    if os.path.commonpath([lower_root_abs, path]) != lower_root_abs:
        raise RuntimeError(f"overlay path escapes lowerdir: {rel!r}")
    return path


def _read_file_bytes(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def _read_xattrs(path: str) -> dict[bytes, bytes]:
    listxattr = getattr(os, "listxattr", None)
    getxattr = getattr(os, "getxattr", None)
    if listxattr is None or getxattr is None:
        return {}
    try:
        names = listxattr(path, follow_symlinks=False)
    except OSError:
        return {}
    out: dict[bytes, bytes] = {}
    for name in names:
        key = name.encode("utf-8") if isinstance(name, str) else name
        try:
            out[key] = getxattr(path, name, follow_symlinks=False)
        except OSError:
            continue
    return out


__all__ = [
    "build_upper_change",
    "is_opaque_dir",
    "is_whiteout",
    "walk_upperdir",
]
