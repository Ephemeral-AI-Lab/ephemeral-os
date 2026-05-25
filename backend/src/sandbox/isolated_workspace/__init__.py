"""Daemon-native isolated workspace feature.

A self-contained directory for the per-agent ``{user, mnt, pid, net}`` sandbox
that the daemon offers via ``api.isolated_workspace.{enter, exit, status}``.

Submodules
----------
- :mod:`.pipeline` — lifecycle state machine, capacity / TTL / host-RAM gate,
  ``manager.json`` persistence, orphan reaping, and phase timing.
- :mod:`.network` — bridge + nftables + per-workspace veth + IP pool.
- :mod:`sandbox.daemon.rpc.dispatcher` — inline lifecycle RPC handlers
  (``api.isolated_workspace.{enter,exit,status,list_open,test_reset}``).
- :mod:`.scripts` — single-threaded subprocess helpers that perform setns
  syscalls. R10 import discipline applies: their module-level import sets
  are pinned by ``test_setns_exec_discipline``.

Cross-package reuse
-------------------
- ``setns_overlay_mount`` calls
  :func:`sandbox.overlay.kernel_mount.mount_overlay` after setns —
  a deferred import keeps R10 (single-thread for ``setns(CLONE_NEWUSER)``).
- Lease / snapshot calls go through
  ``sandbox.daemon.layer_stack_runtime`` (layer-stack-only; OCC is unreachable).
"""

from sandbox.isolated_workspace._control_plane.pipeline_state import (
    AuditSink,
    IsolatedWorkspaceError,
    IsolatedWorkspaceHandle,
)
from sandbox.isolated_workspace.pipeline import IsolatedPipeline

__all__ = [
    "AuditSink",
    "IsolatedPipeline",
    "IsolatedWorkspaceError",
    "IsolatedWorkspaceHandle",
]
