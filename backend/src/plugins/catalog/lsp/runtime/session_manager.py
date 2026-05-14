"""Layer-stack-root keyed cache of stable Pyright sessions."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import tempfile
from pathlib import Path
from typing import Any

from plugins.catalog.lsp.runtime.pyright_session import PyrightSession

__all__ = ["get_session", "evict_all", "evict_for_root"]


logger = logging.getLogger(__name__)


_sessions: dict[str, PyrightSession] = {}
_locks: dict[str, asyncio.Lock] = {}


async def get_session(ctx: Any) -> PyrightSession:
    """Return a Pyright session reconciled to the active manifest."""
    layer_stack_root = str(ctx.layer_stack_root)
    workspace_root = str(getattr(ctx, "metadata", {}).get("workspace_root", ""))
    lock = _locks.setdefault(layer_stack_root, asyncio.Lock())
    async with lock:
        active_key = ctx.projection.active_manifest_key()
        cached = _sessions.get(layer_stack_root)
        if (
            cached is not None
            and workspace_root
            and cached.workspace_root != workspace_root
        ):
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
        if cached is not None and cached.manifest_key == active_key:
            return cached

        handle = ctx.projection.acquire(_owner_request_id(ctx))
        if cached is not None:
            try:
                await cached.refresh_manifest(
                    manifest_key=handle.manifest_key,
                    lowerdir=handle.lowerdir,
                    projection_handle=handle,
                )
                return cached
            except Exception:
                logger.warning(
                    "pyright session refresh failed; restarting",
                    exc_info=True,
                )
                await cached.evict()
                handle = ctx.projection.acquire(_owner_request_id(ctx))
            _sessions.pop(layer_stack_root, None)

        try:
            session = PyrightSession(
                manifest_key=handle.manifest_key,
                lowerdir=handle.lowerdir,
                workspace_root=workspace_root,
                projection_handle=handle,
                stable_root=_stable_root_for(layer_stack_root),
            )
        except Exception:
            handle.release()
            raise
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


def _stable_root_for(layer_stack_root: str) -> str:
    digest = hashlib.sha256(layer_stack_root.encode("utf-8")).hexdigest()[:16]
    return str(Path(tempfile.gettempdir()) / "eos-lsp-workspaces" / digest / "root")
