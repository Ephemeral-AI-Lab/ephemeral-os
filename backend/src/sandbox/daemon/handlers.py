"""Daemon RPC handler bodies — consolidated from ``daemon/handler/*.py``.

Phase 2.6 C4 collapse: the 10 handler modules that previously lived under
``backend/src/sandbox/daemon/handler/`` are folded into this single module
(plus 5 ``_iws_*`` functions inline in :mod:`sandbox.daemon.rpc.dispatcher`
that handle the ``api.isolated_workspace.*`` lifecycle ops).

Module layout:

* In-flight registry surface — ``cancel``, ``heartbeat``, ``inflight_count``.
* Tool primitive shims — ``read_file``, ``write_file``, ``edit_file``,
  ``glob``, ``grep``, ``shell``. Each thin wrapper threads ``args`` and the
  static verb/intent pair through :func:`sandbox.daemon.dispatch.run_tool_handler`.
* Layer-stack diagnostic surface — ``layer_metrics``, ``runtime_ready``.
* Layer-stack control surface — ``build_workspace_base``, ``ensure_workspace_base``,
  ``workspace_binding``, ``prepare_workspace_snapshot``, ``release_lease``,
  ``release_workspace_snapshot`` (legacy alias, see §11 follow-up 12),
  ``fence_stale_staging``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from collections.abc import Callable
from dataclasses import fields
from typing import Any

from sandbox._shared.clock import monotonic_now
from sandbox._shared.models import Intent
from sandbox.daemon import occ_backend, workspace_server
from sandbox.daemon.dispatch import run_tool_handler
from sandbox.daemon.occ_backend import OccBackend
from sandbox.daemon.request_context import (
    require_arg,
    require_layer_stack_root,
)
from sandbox.daemon.rpc.in_flight import get_in_flight_registry
from sandbox.layer_stack.manifest import (
    manifest_path,
    read_manifest,
)
from sandbox.layer_stack.workspace_binding import (
    read_workspace_binding,
    require_workspace_binding,
)
from sandbox.overlay.namespace_runner import detect_private_mount_namespace


_logger = logging.getLogger("sandbox.daemon.handlers")
_CANCEL_CLEANUP_WAIT_S = 5.0
_STARTED_AT_MONO = time.monotonic()


# ---------------------------------------------------------------------------
# In-flight registry surface (api.v1.{cancel, heartbeat, inflight_count})
# ---------------------------------------------------------------------------


async def cancel(args: dict[str, Any]) -> dict[str, object]:
    invocation_id = str(args.get("invocation_id") or "").strip()
    task = get_in_flight_registry().cancel_task(invocation_id)
    cancelled = task is not None
    if task is not None:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await asyncio.wait_for(
                asyncio.shield(task),
                timeout=_CANCEL_CLEANUP_WAIT_S,
            )
    return {
        "success": True,
        "invocation_id": invocation_id,
        "cancelled": cancelled,
        "already_done": not cancelled,
        "cleanup_done": task.done() if task is not None else True,
    }


async def heartbeat(args: dict[str, Any]) -> dict[str, object]:
    raw_ids = args.get("invocation_ids") or []
    invocation_ids = [str(value) for value in raw_ids] if isinstance(raw_ids, list) else []
    touched = get_in_flight_registry().heartbeat(invocation_ids)
    return {"success": True, "touched": touched}


async def inflight_count(args: dict[str, Any]) -> dict[str, object]:
    agent_id = str(args.get("agent_id") or "").strip()
    count = get_in_flight_registry().count_by_agent(agent_id)
    return {"success": True, "agent_id": agent_id, "count": count}


# ---------------------------------------------------------------------------
# Tool primitive shims (api.v1.{read_file, write_file, edit_file, glob, grep, shell})
# ---------------------------------------------------------------------------


async def edit_file(args: dict[str, Any]) -> dict[str, object]:
    return await run_tool_handler(args, verb="edit_file", intent=Intent.WRITE_ALLOWED)


async def glob(args: dict[str, Any]) -> dict[str, object]:
    return await run_tool_handler(args, verb="glob", intent=Intent.READ_ONLY)


async def grep(args: dict[str, Any]) -> dict[str, object]:
    return await run_tool_handler(args, verb="grep", intent=Intent.READ_ONLY)


async def read_file(args: dict[str, Any]) -> dict[str, object]:
    return await run_tool_handler(args, verb="read_file", intent=Intent.READ_ONLY)


async def shell(args: dict[str, Any]) -> dict[str, object]:
    return await run_tool_handler(args, verb="shell", intent=Intent.WRITE_ALLOWED)


async def write_file(args: dict[str, Any]) -> dict[str, object]:
    return await run_tool_handler(args, verb="write_file", intent=Intent.WRITE_ALLOWED)


# ---------------------------------------------------------------------------
# Layer-stack diagnostic surface (api.layer_metrics, api.runtime.ready)
# ---------------------------------------------------------------------------


async def layer_metrics(args: dict[str, object]) -> dict[str, object]:
    """Summarize layer-stack storage and lease state for one runtime root."""
    root = require_layer_stack_root(args)
    manager = occ_backend.build_occ_backend(root).manager
    manifest = manager.read_active_manifest()
    binding = read_workspace_binding(root)
    layer_dirs = tuple((manager.storage_root / "layers").iterdir())
    staging_dirs = tuple((manager.storage_root / "staging").iterdir())
    total_bytes = 0
    for entry in manager.storage_root.rglob("*"):
        if entry.is_file() or entry.is_symlink():
            total_bytes += entry.lstat().st_size
    return {
        "success": True,
        "manifest_version": manifest.version,
        "manifest_depth": manifest.depth,
        "active_leases": manager.active_lease_count(),
        "pinned_layers": len(manager.pinned_layers()),
        "layer_dirs": len(layer_dirs),
        "staging_dirs": len(staging_dirs),
        "storage_bytes": total_bytes,
        "workspace_bound": binding is not None,
        "workspace_root": binding.workspace_root if binding is not None else "",
        "base_root_hash": binding.base_root_hash if binding is not None else "",
    }


def runtime_ready(args: dict[str, object]) -> dict[str, object]:
    """Return binary daemon readiness plus per-plane probe details."""
    total_start = monotonic_now()
    root = require_layer_stack_root(args)
    timings: dict[str, float] = {}
    probes = [
        _run_probe("control_plane", lambda: _probe_control_plane(root), timings=timings),
        _run_probe("data_plane", lambda: _probe_data_plane(root), timings=timings),
        _run_probe("mutation_gate", lambda: _probe_mutation_gate(root), timings=timings),
    ]
    return {
        "success": True,
        "ready": all(probe["status"] == "ok" for probe in probes),
        "probes": probes,
        "daemon_pid": os.getpid(),
        "uptime_s": max(0.0, time.monotonic() - _STARTED_AT_MONO),
        "timings": {
            **timings,
            "runtime.ready.total_s": monotonic_now() - total_start,
        },
    }


def _probe_control_plane(layer_stack_root: str) -> dict[str, object]:
    binding = require_workspace_binding(layer_stack_root)
    manager = workspace_server.get_layer_stack_manager(layer_stack_root)
    manifest = read_manifest(manifest_path(layer_stack_root))
    # Also exercise the manager API; this catches a broken manager cache even
    # when the manifest file itself can be read directly.
    manager_manifest = manager.read_active_manifest()
    if manager_manifest.version != manifest.version:
        raise RuntimeError(
            "manager manifest version does not match active manifest file"
        )
    return {
        "workspace_root": binding.workspace_root,
        "manifest_version": manifest.version,
        "manifest_depth": manifest.depth,
        "base_root_hash": binding.base_root_hash,
    }


def _probe_data_plane(layer_stack_root: str) -> dict[str, object]:
    handlers_backend = occ_backend.build_occ_backend(layer_stack_root)
    if not isinstance(handlers_backend, OccBackend):
        raise RuntimeError(
            "handler services returned "
            f"{type(handlers_backend).__name__}; expected OccBackend"
        )
    mount_mode = "private_namespace" if detect_private_mount_namespace() else "unavailable"
    return {
        "handlers_services_ready": True,
        "shell_services_ready": True,
        "workspace_mount_mode": mount_mode,
    }


def _probe_mutation_gate(layer_stack_root: str) -> dict[str, object]:
    backend = occ_backend.build_occ_backend(layer_stack_root)
    if not isinstance(backend, OccBackend):
        raise RuntimeError(
            f"OCC backend type mismatch: {type(backend).__name__}"
        )
    present_fields = [field.name for field in fields(OccBackend)]
    return {
        "backend_ready": True,
        "backend_fields": present_fields,
        "occ_client_class": type(getattr(backend, "occ_client", None)).__name__,
    }


def _run_probe(
    name: str,
    probe: Callable[[], dict[str, object]],
    *,
    timings: dict[str, float],
) -> dict[str, object]:
    start = monotonic_now()
    try:
        details = probe()
        status = "ok"
    except Exception as exc:
        status = "down"
        details = {
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    timings[f"runtime.ready.{name}_s"] = monotonic_now() - start
    return {
        "name": name,
        "status": status,
        "details": details,
    }


# ---------------------------------------------------------------------------
# Layer-stack control surface (api.{ensure,build}_workspace_base,
# api.workspace_binding, api.prepare_workspace_snapshot, api.release_lease,
# api.release_workspace_snapshot legacy alias, api.layer_stack.fence_stale_staging)
# ---------------------------------------------------------------------------


async def build_workspace_base(args: dict[str, object]) -> dict[str, object]:
    """Build (or rebuild on ``reset``) the layer-stack workspace base.

    ``reset=True`` drops peer runtime caches before rebuilding so the new
    base is rebound cleanly; that side effect is part of the public
    contract, not an internal optimization.
    """
    total_start = monotonic_now()
    layer_stack_root = require_layer_stack_root(args)
    workspace_root = require_arg(args, "workspace_root")
    reset = bool(args.get("reset", False))
    if reset:
        await _drop_peer_runtime_caches(
            layer_stack_root,
            workspace_root=workspace_root,
        )
    timings: dict[str, float] = {}
    binding = workspace_server.build_workspace_base(
        layer_stack_root,
        workspace_root=workspace_root,
        reset=reset,
        timings=timings,
    )
    return {
        "success": True,
        "created": True,
        "binding": binding.to_dict(),
        "timings": {
            **timings,
            "api.workspace_base.total_s": monotonic_now() - total_start,
        },
    }


async def ensure_workspace_base(args: dict[str, object]) -> dict[str, object]:
    total_start = monotonic_now()
    binding, created = workspace_server.ensure_workspace_base(
        require_layer_stack_root(args),
        workspace_root=require_arg(args, "workspace_root"),
    )
    return {
        "success": True,
        "created": created,
        "binding": binding.to_dict(),
        "timings": {
            "api.workspace_base.total_s": monotonic_now() - total_start,
        },
    }


async def workspace_binding(args: dict[str, object]) -> dict[str, object]:
    binding = require_workspace_binding(require_layer_stack_root(args))
    return {
        "success": True,
        "binding": binding.to_dict(),
    }


async def prepare_workspace_snapshot(args: dict[str, object]) -> dict[str, object]:
    total_start = monotonic_now()
    result = workspace_server.prepare_workspace_snapshot(
        require_layer_stack_root(args),
        owner_request_id=require_arg(args, "request_id"),
    )
    payload = result.to_dict()
    timings = payload.get("timings")
    if not isinstance(timings, dict):
        timings = {}
    payload["timings"] = {
        **timings,
        "api.prepare_workspace_snapshot.total_s": monotonic_now() - total_start,
    }
    return {
        "success": True,
        **payload,
    }


async def release_lease(args: dict[str, object]) -> dict[str, object]:
    released = workspace_server.release_lease(
        require_layer_stack_root(args),
        lease_id=require_arg(args, "lease_id"),
    )
    return {
        "success": True,
        "released": released,
    }


async def release_workspace_snapshot(args: dict[str, object]) -> dict[str, object]:
    """Deprecated alias kept for one release cycle.

    Plan §5.5 (C3.5a) rolls ``api.release_workspace_snapshot`` over to
    ``api.release_lease``. The alias delegates to the new handler and
    emits a WARN with ``deprecated_alias`` so callers see exactly which
    verb to migrate. Remove with follow-up §11 item 12.
    """
    _logger.warning(
        "deprecated_alias=api.release_workspace_snapshot use=api.release_lease",
    )
    return await release_lease(args)


async def fence_stale_staging(args: dict[str, object]) -> dict[str, object]:
    return workspace_server.fence_stale_staging(require_layer_stack_root(args))


async def _drop_peer_runtime_caches(
    layer_stack_root: str,
    *,
    workspace_root: str,
) -> None:
    from sandbox.ephemeral_workspace.pipeline import stop_sandbox_overlay

    await stop_sandbox_overlay(layer_stack_root, workspace_root=workspace_root)
    occ_backend.drop_backend_cache(layer_stack_root)


__all__ = [
    "build_workspace_base",
    "cancel",
    "edit_file",
    "ensure_workspace_base",
    "fence_stale_staging",
    "glob",
    "grep",
    "heartbeat",
    "inflight_count",
    "layer_metrics",
    "prepare_workspace_snapshot",
    "read_file",
    "release_lease",
    "release_workspace_snapshot",
    "runtime_ready",
    "shell",
    "workspace_binding",
    "write_file",
]
