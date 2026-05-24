"""Cached capability probe for the overlay new mount API."""

from __future__ import annotations

import os
import logging

from sandbox.overlay.new_mount_api import probe_supported

logger = logging.getLogger(__name__)
_REQUIRE_NEW_MOUNT_API_ENV = "EOS_REQUIRE_NEW_MOUNT_API"


def new_mount_api_supported() -> bool:
    """Return True if the new mount API is available."""
    return probe_supported()


def new_mount_api_required() -> bool:
    """Return whether startup must fail when the new mount API is missing."""
    return os.environ.get(_REQUIRE_NEW_MOUNT_API_ENV, "1").strip() != "0"


def require_new_mount_api() -> None:
    """Enforce the Phase 1 namespace-only startup precondition."""
    if new_mount_api_supported():
        return
    if new_mount_api_required():
        raise RuntimeError(
            "overlay new mount API is unavailable; set "
            "EOS_REQUIRE_NEW_MOUNT_API=0 only during the rollout window"
        )
    logger.warning(
        "overlay new mount API unavailable but EOS_REQUIRE_NEW_MOUNT_API=0; "
        "sandbox is running in degraded rollout mode"
    )


__all__ = [
    "new_mount_api_required",
    "new_mount_api_supported",
    "require_new_mount_api",
]
