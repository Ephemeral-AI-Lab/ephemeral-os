"""Cached capability probe for required Linux overlay mount syscalls."""

from __future__ import annotations

from sandbox.overlay.mount_syscalls import probe_supported


def mount_syscalls_supported() -> bool:
    """Return True if fsopen/fsconfig/fsmount/move_mount are available."""
    return probe_supported()


def require_mount_syscalls() -> None:
    """Enforce the namespace-only startup precondition."""
    if mount_syscalls_supported():
        return
    raise RuntimeError(
        "overlay mount syscalls are unavailable; sandbox startup requires "
        "fsopen/fsconfig/fsmount and private mount namespaces"
    )


__all__ = [
    "mount_syscalls_supported",
    "require_mount_syscalls",
]
