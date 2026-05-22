"""Helpers for the isolated_workspace live test tiers.

Pure functions used by individual tests; the pytest fixtures that wrap them
live in :mod:`conftest`. Each helper exists because a current test calls it.
"""

from __future__ import annotations

import asyncio
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


# ---------------------------------------------------------------------------
# Daemon-restart + host-side state inspection for the GC tier.
# ---------------------------------------------------------------------------


async def iws_scratch_root(sandbox_id: str) -> str:
    """Discover the daemon's iws scratch_root by listing common candidates.

    The daemon picks ``/eos-mount-scratch/eos-sandbox-runtime`` when that path
    is a writable tmpfs and falls back to the layer_stack_root (``/testbed``)
    otherwise. Test code can't know which one is active without inspecting
    the live container.
    """
    from sandbox.api import raw_exec

    result = await raw_exec(
        sandbox_id,
        "find /eos-mount-scratch /testbed -maxdepth 6 -type d "
        "-name 'isolated-workspace' 2>/dev/null | head -1",
        cwd="/",
        timeout=20,
    )
    return (getattr(result, "stdout", "") or "").strip()


async def daemon_kill_and_respawn(
    sandbox_id: str,
    *,
    layer_stack_root: str,
    bootstrap_agent_id: str = "agent-restart-bootstrap",
    poll_interval_s: float = 0.5,
    timeout_s: float = 60.0,
) -> None:
    """SIGKILL the daemon then trigger a respawn so ``startup_gc`` runs.

    Steps:

    1. SIGKILL ``python -m sandbox.daemon`` so its in-memory state is lost
       without an orderly shutdown (the abnormal-exit case Tier 7 cares
       about).
    2. Wait briefly for the process to vanish.
    3. Issue an ``api.isolated_workspace.enter`` RPC for a throwaway agent —
       this triggers ``_ensure_manager`` which calls
       ``IsolatedWorkspaceManager.initialize() → startup_gc()``.
    4. ``exit_`` the throwaway agent so the post-test cleanup stays sane.
    """
    from sandbox.api import raw_exec
    from sandbox.host.daemon_client import call_daemon_api

    await raw_exec(
        sandbox_id,
        "pkill -9 -f '^.*python.*-m sandbox\\.daemon' || true",
        cwd="/",
        timeout=10,
    )
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        probe = await raw_exec(
            sandbox_id,
            "pgrep -f '^.*python.*-m sandbox\\.daemon' >/dev/null && echo UP || echo DOWN",
            cwd="/",
            timeout=10,
        )
        if "DOWN" in (getattr(probe, "stdout", "") or ""):
            break
        await asyncio.sleep(poll_interval_s)

    # The first daemon RPC after kill respawns the process via launch_daemon.sh.
    # Use ``enter`` so ``_ensure_manager`` fires (status/exit don't bootstrap).
    response = await call_daemon_api(
        sandbox_id,
        "api.isolated_workspace.enter",
        {"agent_id": bootstrap_agent_id, "layer_stack_root": layer_stack_root},
        timeout=int(timeout_s),
    )
    # Best-effort cleanup of the bootstrap handle.
    if response.get("success"):
        await call_daemon_api(
            sandbox_id,
            "api.isolated_workspace.exit",
            {"agent_id": bootstrap_agent_id},
            timeout=30,
        )


async def list_host_eos_iws_resources(sandbox_id: str) -> dict[str, list[str]]:
    """Snapshot the live container's iws-named resources for orphan checks."""
    from sandbox.api import raw_exec

    queries = {
        "veth": "ip -o link show 2>/dev/null | awk -F': ' '{print $2}' "
                "| awk '{print $1}' | sed 's/@.*//' | grep '^eos-iws-' || true",
        "cgroup": "ls -1 /sys/fs/cgroup/ 2>/dev/null | grep '^eos-iws-' || true",
        "netns": "ip netns list 2>/dev/null | awk '{print $1}' | grep '^eos-iws-' || true",
    }
    snapshot: dict[str, list[str]] = {}
    for kind, cmd in queries.items():
        result = await raw_exec(sandbox_id, cmd, cwd="/", timeout=15)
        lines = [
            line.strip()
            for line in (getattr(result, "stdout", "") or "").splitlines()
            if line.strip()
        ]
        snapshot[kind] = lines
    return snapshot


async def read_manager_json(
    sandbox_id: str,
    *,
    scratch_root: str,
) -> str:
    """Cat the daemon's persisted ``manager.json`` into a host-visible string."""
    from sandbox.api import raw_exec

    result = await raw_exec(
        sandbox_id,
        f"cat {scratch_root}/manager.json 2>/dev/null || true",
        cwd="/",
        timeout=15,
    )
    return getattr(result, "stdout", "") or ""


async def set_daemon_env(
    sandbox_id: str,
    *,
    pairs: dict[str, str],
    layer_stack_root: str,
) -> None:
    """Write env knobs into ``/etc/environment`` then respawn the daemon.

    Each key in ``pairs`` is appended (or replaced) in ``/etc/environment``;
    the bash login shell that ``launch_daemon.sh`` runs under sources that
    file via PAM, so the next daemon process inherits the new values.
    Use ``clear_daemon_env`` to roll back.
    """
    from sandbox.api import raw_exec

    for key, value in pairs.items():
        await raw_exec(
            sandbox_id,
            f"sed -i '/^{key}=/d' /etc/environment 2>/dev/null; "
            f"echo '{key}={value}' >> /etc/environment",
            cwd="/", timeout=10,
        )
    await daemon_kill_and_respawn(sandbox_id, layer_stack_root=layer_stack_root)


async def clear_daemon_env(
    sandbox_id: str,
    *,
    keys: list[str],
    layer_stack_root: str,
) -> None:
    """Strip env knobs from ``/etc/environment`` and respawn the daemon."""
    from sandbox.api import raw_exec

    for key in keys:
        await raw_exec(
            sandbox_id,
            f"sed -i '/^{key}=/d' /etc/environment 2>/dev/null || true",
            cwd="/", timeout=10,
        )
    await daemon_kill_and_respawn(sandbox_id, layer_stack_root=layer_stack_root)


async def write_manager_json(
    sandbox_id: str,
    *,
    scratch_root: str,
    payload: str,
) -> None:
    """Overwrite ``manager.json`` with arbitrary content (Tier 7 schema tests)."""
    from sandbox.api import raw_exec
    import base64 as _b64

    encoded = _b64.b64encode(payload.encode("utf-8")).decode("ascii")
    await raw_exec(
        sandbox_id,
        f"mkdir -p {scratch_root} && echo '{encoded}' | base64 -d > {scratch_root}/manager.json",
        cwd="/",
        timeout=15,
    )


__all__ = [
    "SentinelFile",
    "can_mount_overlay_natively",
    "clear_daemon_env",
    "daemon_kill_and_respawn",
    "has_cgroup_freezer",
    "has_unshare_netns",
    "iws_scratch_root",
    "list_host_eos_iws_resources",
    "peer_publish_file",
    "publish_sentinel",
    "read_manager_json",
    "set_daemon_env",
    "write_manager_json",
]
