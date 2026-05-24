"""Generic daemon invocation lifecycle handlers."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from sandbox.daemon.rpc.in_flight import get_in_flight_registry

_CANCEL_CLEANUP_WAIT_S = 5.0


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


__all__ = ["cancel", "heartbeat", "inflight_count"]
