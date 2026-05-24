"""Writable overlay directory allocation.

Overlayfs needs a writable ``upperdir`` plus a sibling ``workdir`` for every
mounted overlay. Lower layers are leased from the layer stack; this module owns
only the upper/work side of the mount.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

OVERLAY_WRITABLE_ROOT = Path("/eos-mount-scratch/eos-sandbox-runtime")


class OverlayWritableRootUnavailable(RuntimeError):
    """Raised when the canonical upper/work root is not available."""


@dataclass(frozen=True)
class OverlayWritableDirs:
    """Per-overlay writable directories created beside each other."""

    run_dir: Path
    upperdir: Path
    workdir: Path


def overlay_writable_root() -> Path:
    """Return the canonical filesystem for overlay ``upperdir``/``workdir``.

    There is intentionally no fallback. Overlayfs requires upper/work dirs to
    live on a filesystem suitable for writable overlay state; Docker-backed
    sandboxes provide that filesystem at ``/eos-mount-scratch``.
    """
    root = OVERLAY_WRITABLE_ROOT
    if not root.is_dir():
        raise OverlayWritableRootUnavailable(
            f"overlay writable root is missing: {root}"
        )
    return root


def allocate_overlay_writable_dirs(run_dir: Path) -> OverlayWritableDirs:
    """Create and return the upper/work dirs for one overlay instance."""
    upperdir = run_dir / "upper"
    workdir = run_dir / "work"
    upperdir.mkdir(parents=True, exist_ok=True)
    workdir.mkdir(parents=True, exist_ok=True)
    return OverlayWritableDirs(run_dir=run_dir, upperdir=upperdir, workdir=workdir)


__all__ = [
    "OVERLAY_WRITABLE_ROOT",
    "OverlayWritableDirs",
    "OverlayWritableRootUnavailable",
    "allocate_overlay_writable_dirs",
    "overlay_writable_root",
]
