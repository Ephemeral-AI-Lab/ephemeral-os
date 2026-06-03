"""Layer-stack-root keyed cache of Pyright sessions for Rust PPC services."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from plugins.catalog.lsp.runtime.pyright_session import PyrightSession

__all__ = ["get_session", "session_audit_counts", "evict_all", "evict_for_root"]


logger = logging.getLogger(__name__)

_sessions: dict[str, PyrightSession] = {}
_locks: dict[str, asyncio.Lock] = {}


async def get_session(ctx: Any) -> PyrightSession:
    """Return a Pyright session reconciled to the daemon manifest key."""
    layer_stack_root = str(ctx.layer_stack_root)
    lock = _locks.setdefault(layer_stack_root, asyncio.Lock())
    async with lock:
        active_key = await _active_manifest_key(ctx)
        workspace_root = _declared_workspace_root(ctx)
        cached = _sessions.get(layer_stack_root)
        if cached is not None and cached.workspace_root != workspace_root:
            logger.info(
                "pyright session workspace root changed; restarting",
                extra={
                    "old_workspace_root": cached.workspace_root,
                    "new_workspace_root": workspace_root,
                },
            )
            await cached.evict()
            _sessions.pop(layer_stack_root, None)
            cached = None
        if cached is not None:
            if cached.manifest_key != active_key:
                await cached.refresh_manifest(
                    manifest_key=active_key,
                    workspace_root=workspace_root,
                )
            return cached

        session = PyrightSession(
            manifest_key=active_key,
            workspace_root=workspace_root,
        )
        _sessions[layer_stack_root] = session
        return session


def session_audit_counts(ctx: Any) -> dict[str, int]:
    """Return cached session counters before a timed operation reconciles it."""
    cached = _sessions.get(str(ctx.layer_stack_root))
    if cached is None:
        return {"start": 0, "refresh": 0, "remount": 0}
    return {
        "start": int(getattr(cached, "audit_start_count", 0)),
        "refresh": int(getattr(cached, "audit_refresh_count", 0)),
        "remount": int(getattr(cached, "audit_remount_count", 0)),
    }


async def evict_for_root(layer_stack_root: str) -> None:
    cached = _sessions.pop(layer_stack_root, None)
    if cached is not None:
        await cached.evict()


async def evict_all() -> None:
    for root in list(_sessions.keys()):
        await evict_for_root(root)


async def _active_manifest_key(ctx: Any) -> str:
    projection = getattr(ctx, "projection", None)
    if projection is not None and hasattr(projection, "active_manifest_key"):
        return str(projection.active_manifest_key())
    overlay = getattr(ctx, "overlay", None)
    ensure_current = getattr(overlay, "ensure_current", None)
    if callable(ensure_current):
        metadata = getattr(ctx, "metadata", None) or {}
        op_name = str(metadata.get("op_name", "tool"))
        return str(await ensure_current(reason=f"lsp:{op_name}:enter"))
    active_manifest_key = getattr(overlay, "active_manifest_key", None)
    if callable(active_manifest_key):
        return str(active_manifest_key())
    return "workspace@0"


def _declared_workspace_root(ctx: Any) -> str:
    overlay = getattr(ctx, "overlay", None)
    metadata = getattr(ctx, "metadata", None) or {}
    return str(
        metadata.get("workspace_root")
        or getattr(overlay, "workspace_root", "")
        or "/testbed"
    )
