"""Cached capability probe for the overlay new mount API.

Call ``new_mount_api_supported()`` at daemon startup and once per
``execute_command`` decision. The underlying ``probe_supported()`` is
``functools.cache``-d so the kernel probe fires at most once per process.

Kill switch: ``EOS_OVERLAY_FORCE_MATERIALIZE=1`` forces the materialize
(classic lowerdir symlink) path regardless of kernel capability.
"""

from __future__ import annotations

import os

from sandbox.execution.overlay.new_mount_api import probe_supported


def new_mount_api_supported() -> bool:
    """Return True if the new mount API is available and the kill switch is off."""
    if os.environ.get("EOS_OVERLAY_FORCE_MATERIALIZE", "").strip() == "1":
        return False
    return probe_supported()


__all__ = ["new_mount_api_supported"]
