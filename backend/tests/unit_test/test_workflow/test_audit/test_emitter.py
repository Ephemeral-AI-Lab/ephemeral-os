"""Tests for workflow audit emitter helpers."""

from __future__ import annotations

from audit.base import AuditEvent
from workflow._core import audit as events
from workflow._core.audit import WorkflowAuditEmitter


class CollectingSink:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def publish(self, event: AuditEvent) -> None:
        self.events.append(event)


def test_task_ready_event_preserves_pending_status_and_dependencies() -> None:
    sink = CollectingSink()
    emitter = WorkflowAuditEmitter(sink)

    emitter.task_ready(
        {
            "task_id": "task-1",
            "request_id": "request-1",
            "role": "generator",
            "agent_name": "generator",
            "needs": ["dep-1", "dep-2"],
        },
        attempt_id="attempt-1",
        satisfied_dependency_ids=("dep-1", "dep-2"),
    )

    event = sink.events[0]
    assert event.source == "workflow"
    assert event.type == events.TASK_READY
    assert event.node.request_id == "request-1"
    assert event.node.task_id == "task-1"
    assert event.node.attempt_id == "attempt-1"
    assert event.payload["status_from"] == "pending"
    assert event.payload["status_to"] == "pending"
    assert event.payload["satisfied_dependency_ids"] == ["dep-1", "dep-2"]


def test_task_failed_event_includes_fail_reason_and_summary() -> None:
    sink = CollectingSink()
    emitter = WorkflowAuditEmitter(sink)

    emitter.task_failed(
        {
            "task_id": "task-1",
            "request_id": "request-1",
            "role": "reducer",
            "agent_name": "reducer",
            "status": "failed",
            "needs": [],
        },
        attempt_id="attempt-1",
        fail_reason="agent_launch_failed",
        summary="Reducer agent launch failed.",
    )

    event = sink.events[0]
    assert event.type == events.TASK_FAILED
    assert event.payload["status_from"] == "running"
    assert event.payload["status_to"] == "failed"
    assert event.payload["fail_reason"] == "agent_launch_failed"
    assert event.payload["summary"] == "Reducer agent launch failed."
