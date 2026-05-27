"""StreamEvent → audit Event translation."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from task_center_runner.audit.bus import AuditEventBus
from task_center_runner.audit.events import Event, EventType
from task_center_runner.audit.node_id import NodeId
from task_center_runner.audit.sandbox_events import (
    sandbox_events_from_tool_completion,
)
from message.events import ToolExecutionCompletedEvent, ToolExecutionStartedEvent

__all__ = ["stream_bridge"]


def stream_bridge(
    bus: AuditEventBus,
    *,
    task_center_run_id: str,
    sandbox_fallback_enabled: bool = True,
) -> Callable[[object], Awaitable[None]]:
    """Return an async on_agent_event callable that translates StreamEvents to audit Events."""

    async def _on_event(stream_event: object) -> None:
        if isinstance(stream_event, ToolExecutionStartedEvent):
            node = NodeId(
                task_center_run_id=task_center_run_id,
                agent_name=stream_event.agent_name or None,
                agent_run_id=stream_event.run_id or None,
                tool_name=stream_event.tool_name or None,
            )
            bus.publish(
                Event(
                    type=EventType.TOOL_CALL_STARTED,
                    node=node,
                    payload={
                        "tool_name": stream_event.tool_name,
                        "tool_input": stream_event.tool_input,
                        "tool_use_id": stream_event.tool_use_id,
                    },
                )
            )
        elif isinstance(stream_event, ToolExecutionCompletedEvent):
            metadata = dict(stream_event.metadata or {})
            node = NodeId(
                task_center_run_id=task_center_run_id,
                agent_name=stream_event.agent_name or None,
                agent_run_id=stream_event.run_id or None,
                tool_name=stream_event.tool_name or None,
            )
            event_type = (
                EventType.TOOL_CALL_ERROR
                if stream_event.is_error
                else EventType.TOOL_CALL_COMPLETED
            )
            bus.publish(
                Event(
                    type=event_type,
                    node=node,
                    payload={
                        "tool_name": stream_event.tool_name,
                        "output": stream_event.output,
                        "is_error": stream_event.is_error,
                        "tool_use_id": stream_event.tool_use_id,
                        "metadata": metadata,
                        "is_terminal": stream_event.is_terminal,
                    },
                )
            )
            if sandbox_fallback_enabled:
                for sandbox_event in sandbox_events_from_tool_completion(
                    stream_event,
                    task_center_run_id=task_center_run_id,
                ):
                    if (
                        not metadata.get("sandbox_audit_emitted")
                        or sandbox_event.type
                        is EventType.SANDBOX_LAYER_STACK_LEASE_ACQUIRED
                    ):
                        bus.publish(sandbox_event)
        # All other StreamEvent subtypes are silently ignored.

    return _on_event
