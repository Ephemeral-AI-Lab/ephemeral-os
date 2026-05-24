"""Generic daemon request lifecycle handlers."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from sandbox.daemon.rpc.in_flight import get_in_flight_registry

_CANCEL_CLEANUP_WAIT_S = 5.0


async def cancel(args: dict[str, Any]) -> dict[str, object]:
    request_id = str(args.get("request_id") or "").strip()
    task = get_in_flight_registry().cancel_task(request_id)
    cancelled = task is not None
    if task is not None:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await asyncio.wait_for(
                asyncio.shield(task),
                timeout=_CANCEL_CLEANUP_WAIT_S,
            )
    return {
        "success": True,
        "request_id": request_id,
        "cancelled": cancelled,
        "already_done": not cancelled,
        "cleanup_done": task.done() if task is not None else True,
    }


async def heartbeat(args: dict[str, Any]) -> dict[str, object]:
    raw_ids = args.get("request_ids") or []
    request_ids = [str(value) for value in raw_ids] if isinstance(raw_ids, list) else []
    touched = get_in_flight_registry().heartbeat(
        request_ids,
        engine_process_id=str(args.get("engine_process_id") or ""),
        engine_started_at=_optional_float(args.get("engine_started_at")),
    )
    return {"success": True, "touched": touched}


async def inflight_count(args: dict[str, Any]) -> dict[str, object]:
    agent_id = str(args.get("agent_id") or args.get("actor_id") or "").strip()
    count = get_in_flight_registry().count_by_agent(agent_id)
    return {"success": True, "agent_id": agent_id, "count": count}


def _optional_float(raw: object) -> float | None:
    if raw is None:
        return None
    try:
        return float(str(raw))
    except (TypeError, ValueError):
        return None


__all__ = ["cancel", "heartbeat", "inflight_count"]
