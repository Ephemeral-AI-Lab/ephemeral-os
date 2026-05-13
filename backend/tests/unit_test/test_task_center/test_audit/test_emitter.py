"""Tests for TaskCenter audit emitter helpers."""

from __future__ import annotations

from audit.base import AuditEvent
from task_center.audit import events
from task_center.audit.emitter import TaskCenterAuditEmitter


class CollectingSink:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def publish(self, event: AuditEvent) -> None:
        self.events.append(event)


def test_task_ready_event_preserves_pending_status_and_dependencies() -> None:
    sink = CollectingSink()
    emitter = TaskCenterAuditEmitter(sink)

    emitter.task_ready(
        {
            "id": "task-1",
            "task_center_run_id": "run-1",
            "task_center_attempt_id": "attempt-1",
            "role": "generator",
            "agent_name": "generator",
            "needs": ["dep-1", "dep-2"],
            "context_packet_id": "packet-1",
        },
        attempt_id="attempt-1",
        satisfied_dependency_ids=("dep-1", "dep-2"),
    )

    event = sink.events[0]
    assert event.source == "task_center"
    assert event.type == events.TASK_READY
    assert event.node.task_center_run_id == "run-1"
    assert event.node.task_center_task_id == "task-1"
    assert event.node.attempt_id == "attempt-1"
    assert event.payload["status_from"] == "pending"
    assert event.payload["status_to"] == "pending"
    assert event.payload["satisfied_dependency_ids"] == ["dep-1", "dep-2"]


def test_task_failed_event_includes_fail_reason_and_summary() -> None:
    sink = CollectingSink()
    emitter = TaskCenterAuditEmitter(sink)

    emitter.task_failed(
        {
            "id": "task-1",
            "task_center_run_id": "run-1",
            "task_center_attempt_id": "attempt-1",
            "role": "evaluator",
            "agent_name": "evaluator",
            "status": "failed",
            "needs": [],
        },
        attempt_id="attempt-1",
        fail_reason="agent_launch_failed",
        summary="Evaluator agent launch failed.",
    )

    event = sink.events[0]
    assert event.type == events.TASK_FAILED
    assert event.payload["status_from"] == "running"
    assert event.payload["status_to"] == "failed"
    assert event.payload["fail_reason"] == "agent_launch_failed"
    assert event.payload["summary"] == "Evaluator agent launch failed."
