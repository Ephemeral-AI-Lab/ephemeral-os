"""Overlay subsystem: shared layout + kernel mount + capture + synthesis.

Overlayfs vocabulary (lowerdir/upperdir/workdir) is confined to the two files
that respectively call the kernel mount and emulate its semantics
(kernel_mount.py, change_synthesis.py). The shared layout uses domain names.
"""

from sandbox.execution.overlay.capture import walk_upperdir
from sandbox.execution.overlay.change_synthesis import synthesize_writes
from sandbox.execution.overlay.kernel_mount import (
    MountInputs,
    mount_overlay,
    umount,
    validate_mount_inputs,
)
from sandbox.execution.overlay.layout import OverlayLayout

__all__ = [
    "MountInputs",
    "OverlayLayout",
    "mount_overlay",
    "synthesize_writes",
    "umount",
    "validate_mount_inputs",
    "walk_upperdir",
]
