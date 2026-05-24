"""Cached capability probe for the overlay new mount API."""

from __future__ import annotations

from sandbox.overlay.new_mount_api import probe_supported


def new_mount_api_supported() -> bool:
    """Return True if the new mount API is available."""
    return probe_supported()


def require_new_mount_api() -> None:
    """Enforce the namespace-only startup precondition."""
    if new_mount_api_supported():
        return
    raise RuntimeError(
        "overlay new mount API is unavailable; sandbox startup requires "
        "fsopen/fsconfig/fsmount and private mount namespaces"
    )


__all__ = [
    "new_mount_api_supported",
    "require_new_mount_api",
]
