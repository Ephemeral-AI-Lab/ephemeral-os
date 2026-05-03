"""Overlay mount option helpers for the depth-100 layer-stack experiment."""

from __future__ import annotations

import ctypes
import errno
import os
from dataclasses import dataclass
from pathlib import Path

from stack_overlay.models import Manifest

DEFAULT_MAX_DEPTH = 100


@dataclass(frozen=True)
class OverlayMountSpec:
    cwd: Path
    lowerdir: str
    upperdir: str
    workdir: str
    merged: str
    options: str


def build_mount_spec(
    *,
    session_root: str | Path,
    manifest: Manifest,
    run_dir: str | Path,
    max_depth: int = DEFAULT_MAX_DEPTH,
    relative_lowerdir: bool = True,
    userxattr: bool = True,
) -> OverlayMountSpec:
    """Build a mount spec that preserves the short-relative lowerdir contract."""

    if manifest.depth > max_depth:
        raise ValueError(f"manifest depth {manifest.depth} exceeds cap {max_depth}")
    root = Path(session_root)
    run = Path(run_dir)
    upper = run / "u"
    work = run / "w"
    merged = run / "m"
    if relative_lowerdir:
        lower_entries = list(manifest.layers)
        cwd = root
    else:
        lower_entries = [str(root / layer) for layer in manifest.layers]
        cwd = Path("/")
    lowerdir = ":".join(lower_entries)
    options = f"lowerdir={lowerdir},upperdir={upper},workdir={work}"
    if userxattr:
        options = f"{options},userxattr"
    return OverlayMountSpec(
        cwd=cwd,
        lowerdir=lowerdir,
        upperdir=str(upper),
        workdir=str(work),
        merged=str(merged),
        options=options,
    )


def mount_overlay_syscall(spec: OverlayMountSpec) -> None:
    """Mount an overlay using the legacy mount(2) data path.

    Daytona's current util-linux ``mount(8)`` can fail on deep overlay
    lowerdir stacks even when the kernel accepts the same options through the
    direct syscall. The live experiment should use this helper, or a tiny helper
    binary with the same syscall shape, rather than shelling out to ``mount``.
    """

    libc = ctypes.CDLL(None, use_errno=True)
    mount = getattr(libc, "mount", None)
    if mount is None:
        raise OSError(errno.ENOSYS, "libc mount(2) is unavailable")
    mount.argtypes = [
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_ulong,
        ctypes.c_char_p,
    ]
    mount.restype = ctypes.c_int

    previous_cwd = Path.cwd()
    try:
        os.chdir(spec.cwd)
        rc = mount(
            b"overlay",
            os.fsencode(spec.merged),
            b"overlay",
            0,
            os.fsencode(spec.options),
        )
    finally:
        os.chdir(previous_cwd)
    if rc != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err))


def unmount_overlay_syscall(target: str | Path) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    umount2 = getattr(libc, "umount2", None)
    if umount2 is None:
        raise OSError(errno.ENOSYS, "libc umount2(2) is unavailable")
    umount2.argtypes = [ctypes.c_char_p, ctypes.c_int]
    umount2.restype = ctypes.c_int
    rc = umount2(os.fsencode(target), 0)
    if rc != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err))
