"""Layer-stack-root keyed cache of Pyright sessions rooted at /testbed."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from plugins.catalog.lsp.runtime.pyright_session import PyrightSession

__all__ = ["get_session", "evict_all", "evict_for_root"]


logger = logging.getLogger(__name__)


_sessions: dict[str, PyrightSession] = {}
_locks: dict[str, asyncio.Lock] = {}
_event_tasks: dict[str, asyncio.Task[None]] = {}
_event_subscriptions: dict[str, tuple[Any, str]] = {}


async def get_session(ctx: Any) -> PyrightSession:
    """Return a Pyright session reconciled to the active daemon overlay."""
    layer_stack_root = str(ctx.layer_stack_root)
    overlay = getattr(ctx, "overlay", None)
    workspace_root = str(
        getattr(overlay, "workspace_root", "")
        or getattr(ctx, "metadata", {}).get("workspace_root", "")
        or "/testbed"
    )
    lock = _locks.setdefault(layer_stack_root, asyncio.Lock())
    async with lock:
        active_key = await _ensure_current(ctx)
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
        if cached is not None:
            if cached.manifest_key != active_key:
                await cached.refresh_manifest(manifest_key=active_key)
            _ensure_event_subscription(layer_stack_root, overlay, cached)
            return cached

        session = PyrightSession(
            manifest_key=active_key,
            workspace_root=workspace_root,
        )
        _sessions[layer_stack_root] = session
        _ensure_event_subscription(layer_stack_root, overlay, session)
        return session


async def evict_for_root(layer_stack_root: str) -> None:
    task = _event_tasks.pop(layer_stack_root, None)
    if task is not None:
        task.cancel()
    subscription = _event_subscriptions.pop(layer_stack_root, None)
    if subscription is not None:
        overlay, subscriber_id = subscription
        event_bus = getattr(overlay, "event_bus", None)
        unsubscribe = getattr(event_bus, "unsubscribe", None)
        if callable(unsubscribe):
            unsubscribe(subscriber_id)
    cached = _sessions.pop(layer_stack_root, None)
    if cached is not None:
        await cached.evict()


async def evict_all() -> None:
    for root in list(_sessions.keys()):
        await evict_for_root(root)


async def _ensure_current(ctx: Any) -> str:
    overlay = getattr(ctx, "overlay", None)
    if overlay is not None and hasattr(overlay, "ensure_current"):
        metadata = getattr(ctx, "metadata", None) or {}
        op_name = str(metadata.get("op_name", "tool"))
        return await overlay.ensure_current(reason=f"lsp:{op_name}:enter")
    projection = getattr(ctx, "projection", None)
    if projection is not None and hasattr(projection, "active_manifest_key"):
        return projection.active_manifest_key()
    return "workspace@0"


def _ensure_event_subscription(
    layer_stack_root: str,
    overlay: Any,
    session: PyrightSession,
) -> None:
    event_bus = getattr(overlay, "event_bus", None)
    subscribe = getattr(event_bus, "subscribe", None)
    if not callable(subscribe):
        return
    existing = _event_subscriptions.get(layer_stack_root)
    if existing is not None and existing[0] is overlay:
        return
    if existing is not None:
        existing_bus = getattr(existing[0], "event_bus", None)
        unsubscribe = getattr(existing_bus, "unsubscribe", None)
        if callable(unsubscribe):
            unsubscribe(existing[1])
    task = _event_tasks.pop(layer_stack_root, None)
    if task is not None:
        task.cancel()
    subscriber_id = f"lsp:{layer_stack_root}"
    queue = subscribe(subscriber_id)
    _event_subscriptions[layer_stack_root] = (overlay, subscriber_id)
    _event_tasks[layer_stack_root] = asyncio.create_task(
        _pump_workspace_events(layer_stack_root, overlay, session, queue)
    )


async def _pump_workspace_events(
    layer_stack_root: str,
    overlay: Any,
    session: PyrightSession,
    queue: asyncio.Queue[Any],
) -> None:
    while True:
        event = await queue.get()
        cached = _sessions.get(layer_stack_root)
        if cached is not session:
            return
        active_key = (
            overlay.active_manifest_key()
            if hasattr(overlay, "active_manifest_key")
            else session.manifest_key
        )
        if active_key != session.manifest_key or getattr(event, "changes", ()):
            await session.refresh_manifest(manifest_key=active_key)
