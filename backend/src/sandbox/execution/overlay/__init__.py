"""Overlay subsystem: shared layout + kernel mount + capture + synthesis.

Overlayfs vocabulary (lowerdir/upperdir/workdir) is confined to the two files
that respectively call the kernel mount and emulate its semantics
(kernel_mount.py, change_synthesis.py). The shared layout uses domain names.
"""

from sandbox.execution.overlay.layout import OverlayLayout

__all__ = ["OverlayLayout"]
