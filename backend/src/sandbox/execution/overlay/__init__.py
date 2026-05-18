"""Overlay subsystem: shared layout + kernel mount + capture + synthesis.

Overlayfs vocabulary (lowerdir/upperdir/workdir) is confined to the two files
that respectively call the kernel mount and emulate its semantics
(kernel_mount.py, change_synthesis.py). The shared layout uses domain names.

Import overlay primitives from their specific submodules (layout,
kernel_mount, capture, change_synthesis) rather than this package root.
"""
