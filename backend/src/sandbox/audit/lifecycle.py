"""Audit helpers for workspace lifecycle operations."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator, Mapping

from audit.jsonl import append_jsonl_event
from sandbox._shared.clock import monotonic_now
from sandbox.audit import events


@dataclass(frozen=True)
class WorkspaceLifecycle:
    kind: str
    agent_id: str
    timings: Mapping[str, float]


@asynccontextmanager
async def lifecycle_operation(
    *,
    kind: str,
    agent_id: str,
    audit_path: str | None = None,
) -> AsyncIterator[dict[str, float]]:
    timings: dict[str, float] = {}
    started = monotonic_now()
    _emit(
        audit_path,
        events.WORKSPACE_LIFECYCLE_STARTED,
        {"kind": kind, "agent_id": agent_id},
    )
    try:
        yield timings
    except Exception as exc:
        timings["workspace_lifecycle.total_s"] = monotonic_now() - started
        _emit(
            audit_path,
            events.WORKSPACE_LIFECYCLE_FAILED,
            {
                "kind": kind,
                "agent_id": agent_id,
                "error": type(exc).__name__,
                "message": str(exc),
                "timings": dict(timings),
            },
        )
        raise
    else:
        timings["workspace_lifecycle.total_s"] = monotonic_now() - started
        _emit(
            audit_path,
            events.WORKSPACE_LIFECYCLE_COMPLETED,
            {"kind": kind, "agent_id": agent_id, "timings": dict(timings)},
        )


def _emit(path: str | None, event_type: str, payload: dict[str, object]) -> None:
    append_jsonl_event(path, {"type": event_type, "payload": payload})


__all__ = ["WorkspaceLifecycle", "lifecycle_operation"]
