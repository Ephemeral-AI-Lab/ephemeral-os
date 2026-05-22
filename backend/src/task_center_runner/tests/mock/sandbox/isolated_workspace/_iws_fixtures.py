"""Helpers for the isolated_workspace live test tiers.

Pure functions used by individual tests; the pytest fixtures that wrap them
live in :mod:`conftest`. Each helper exists because a current test calls it
— Tier 3+ probes (host-side HTTP server, ``unshare -n`` reachability) are
intentionally absent here and will be added in the PR that lands the
corresponding tier.
"""

from __future__ import annotations

import subprocess
import uuid
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SentinelFile:
    """A file published into the default layer via the peer flow.

    Tests that exercise lowerdir pinning use this to prove the workspace
    snapshot sees the pre-enter view, not the post-enter view.
    """

    path: str
    body: str


async def peer_publish_file(
    sandbox_id: str,
    *,
    path: str,
    body: str,
) -> None:
    """Publish a file through the default flow (``api.write_file`` + flush)."""
    from sandbox.host.daemon_client import call_daemon_api

    await call_daemon_api(
        sandbox_id,
        "api.write_file",
        {"path": path, "content": body},
        timeout=30,
    )
    await call_daemon_api(
        sandbox_id,
        "api.overlay.flush",
        {},
        timeout=30,
    )


async def publish_sentinel(sandbox_id: str) -> SentinelFile:
    token = uuid.uuid4().hex[:12]
    sentinel = SentinelFile(
        path=f"/testbed/sentinel-{token}.txt",
        body=f"lowerdir-visible-{token}",
    )
    await peer_publish_file(sandbox_id, path=sentinel.path, body=sentinel.body)
    return sentinel


# ---------------------------------------------------------------------------
# Capability probes (v2 §18). Each delegates to the canonical implementation
# in ``sandbox.execution.overlay.capability`` where one exists.
# ---------------------------------------------------------------------------


def can_mount_overlay_natively() -> bool:
    """Probe whether the kernel supports the modern overlay mount API.

    Delegates to :func:`sandbox.execution.overlay.capability.new_mount_api_supported`
    so the iws path shares the same probe (and ``EOS_OVERLAY_FORCE_MATERIALIZE``
    kill-switch) as the daemon's OCC overlay. Cached at the underlying layer.
    """
    from sandbox.execution.overlay.capability import new_mount_api_supported

    return new_mount_api_supported()


def has_cgroup_freezer() -> bool:
    try:
        with open("/sys/fs/cgroup/cgroup.controllers", "r", encoding="utf-8") as fh:
            return "freezer" in fh.read()
    except OSError:
        return False


def has_unshare_netns() -> bool:
    try:
        result = subprocess.run(
            ["unshare", "-n", "true"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


__all__ = [
    "SentinelFile",
    "can_mount_overlay_natively",
    "has_cgroup_freezer",
    "has_unshare_netns",
    "peer_publish_file",
    "publish_sentinel",
]
