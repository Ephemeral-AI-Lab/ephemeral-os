"""Manifest-keyed cache of Pyright sessions per layer-stack root."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from plugins.catalog.lsp.runtime.pyright_session import PyrightSession

__all__ = ["get_session", "evict_all", "evict_for_root"]


logger = logging.getLogger(__name__)


_sessions: dict[str, PyrightSession] = {}
_locks: dict[str, asyncio.Lock] = {}


async def get_session(ctx: Any) -> PyrightSession:
    """Return a Pyright session valid for the active manifest.

    On manifest_key change for the same layer_stack_root, evicts the prior
    session (terminates the subprocess and releases the projection lease)
    and starts a fresh one bound to the new lowerdir.
    """
    layer_stack_root = str(ctx.layer_stack_root)
    lock = _locks.setdefault(layer_stack_root, asyncio.Lock())
    async with lock:
        active_key = ctx.projection.active_manifest_key()
        cached = _sessions.get(layer_stack_root)
        if cached is not None and cached.manifest_key == active_key:
            return cached
        if cached is not None:
            await cached.evict()
            _sessions.pop(layer_stack_root, None)
        handle = ctx.projection.acquire(_owner_request_id(ctx))
        workspace_root = str(getattr(ctx, "metadata", {}).get("workspace_root", ""))
        session = PyrightSession(
            manifest_key=handle.manifest_key,
            lowerdir=handle.lowerdir,
            workspace_root=workspace_root,
            projection_handle=handle,
        )
        _sessions[layer_stack_root] = session
        return session


async def evict_for_root(layer_stack_root: str) -> None:
    cached = _sessions.pop(layer_stack_root, None)
    if cached is not None:
        await cached.evict()


async def evict_all() -> None:
    for root in list(_sessions.keys()):
        await evict_for_root(root)


def _owner_request_id(ctx: Any) -> str:
    caller = getattr(ctx, "caller", None)
    if caller is not None:
        agent_run_id = getattr(caller, "agent_run_id", "") or ""
        if agent_run_id:
            return f"lsp:{agent_run_id}"
        agent_id = getattr(caller, "agent_id", "") or ""
        if agent_id:
            return f"lsp:{agent_id}"
    return "lsp"
