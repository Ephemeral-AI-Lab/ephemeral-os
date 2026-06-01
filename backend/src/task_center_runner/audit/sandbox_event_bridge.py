"""Bridge sandbox-owned audit events into the runner audit bus."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from audit.base import AuditEvent, AuditSink
from sandbox.audit import events as sandbox_events
from task_center_runner.audit.bus import AuditEventBus
from task_center_runner.audit.events import Event, EventType
from task_center_runner.audit.node_id import NodeId


_SANDBOX_EVENT_MAP = {
    sandbox_events.OPERATION_CONFLICTED: EventType.SANDBOX_CONFLICT_DETECTED,
    sandbox_events.OCC_PREPARED: EventType.SANDBOX_OCC_CHANGESET_RECEIVED,
    sandbox_events.OCC_COMMITTED: EventType.SANDBOX_OCC_CHANGES_COMMITTED,
    sandbox_events.OCC_CONFLICTED: EventType.SANDBOX_CONFLICT_DETECTED,
    sandbox_events.OVERLAY_EXECUTED: EventType.SANDBOX_OVERLAY_EXECUTED,
    sandbox_events.LAYER_STACK_LEASE_ACQUIRED: (
        EventType.SANDBOX_LAYER_STACK_LEASE_ACQUIRED
    ),
    sandbox_events.LAYER_STACK_LAYER_PUBLISHED: (
        EventType.SANDBOX_LAYER_STACK_LAYER_CREATED
    ),
    sandbox_events.LAYER_STACK_AUTO_SQUASHED: (
        EventType.SANDBOX_LAYER_STACK_LAYERS_SQUASHED
    ),
    sandbox_events.RESOURCE_SNAPSHOT: EventType.SANDBOX_RESOURCE_SNAPSHOT,
}


class SandboxAuditEventBridge(AuditSink):
    """Forward sandbox-owned audit events into ``AuditEventBus``."""

    def __init__(self, bus: AuditEventBus) -> None:
        self._bus = bus

    def publish(self, event: AuditEvent) -> None:
        for runner_event in runner_events_from_sandbox_audit_event(event):
            self._bus.publish(runner_event)


def runner_events_from_sandbox_audit_event(event: AuditEvent) -> tuple[Event, ...]:
    """Return runner audit events for one sandbox-owned audit event."""
    if event.source != "sandbox":
        return ()
    event_type = _SANDBOX_EVENT_MAP.get(event.type)
    if event_type is None:
        return ()
    return (
        Event(
            type=event_type,
            node=_runner_node(event),
            payload=_runner_payload(event),
            correlation_id=event.correlation_id,
            ts=event.ts,
        ),
    )


def _runner_node(event: AuditEvent) -> NodeId:
    node = event.node
    return NodeId(
        request_id=node.request_id or "",
        workflow_id=node.workflow_id,
        iteration_id=node.iteration_id,
        attempt_id=node.attempt_id,
        agent_name=node.agent_name,
        agent_run_id=node.agent_run_id or node.task_center_task_id,
        tool_name=node.tool_name,
    )


def _runner_payload(event: AuditEvent) -> dict[str, Any]:
    payload = dict(event.payload)
    if "tool_name" not in payload and event.node.tool_name:
        payload["tool_name"] = event.node.tool_name
    if "tool_use_id" not in payload and event.node.tool_use_id:
        payload["tool_use_id"] = event.node.tool_use_id
    changed_paths = payload.get("changed_paths")
    if isinstance(changed_paths, Iterable) and not isinstance(
        changed_paths, (str, bytes, dict)
    ):
        payload["changed_paths"] = [str(path) for path in changed_paths]
    return payload


__all__ = ["SandboxAuditEventBridge", "runner_events_from_sandbox_audit_event"]
