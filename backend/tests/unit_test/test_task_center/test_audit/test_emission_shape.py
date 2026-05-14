"""Regression test for audit emission dict-shape invariance after lever #18.

Lever #18 (plan iter4 §Phase 1) deleted four unused dataclasses
(_BaseTaskPayload, TaskReadyPayload, TaskLaunchedPayload, TaskFailedPayload)
from task_center.audit. The emit sites were already constructing payload
dicts inline — the deletion is mechanically inert for emission shape, but
the regression test pins the dict-key set so future edits cannot drop keys
without surfacing here.

Plan: .omc/plans/task-center-folder-reframe-20260514.md (lever #18, AC #12)
"""

from __future__ import annotations

from audit.base import AuditEvent
from task_center import audit as events
from task_center.audit import TaskCenterAuditEmitter


class CollectingSink:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def publish(self, event: AuditEvent) -> None:
        self.events.append(event)


_TASK = {
    "id": "task-1",
    "task_center_run_id": "run-1",
    "task_center_attempt_id": "attempt-1",
    "role": "generator",
    "agent_name": "generator",
    "needs": ["dep-1"],
    "context_packet_id": "packet-1",
    "status": "running",
}

_BASE_KEYS = frozenset(
    {
        "run_id",
        "attempt_id",
        "task_center_task_id",
        "role",
        "agent_name",
        "needs",
        "context_packet_id",
        "status_from",
        "status_to",
    }
)


def test_task_ready_payload_shape_is_stable() -> None:
    sink = CollectingSink()
    TaskCenterAuditEmitter(sink).task_ready(
        _TASK, attempt_id="attempt-1", satisfied_dependency_ids=("dep-1",)
    )
    payload = sink.events[0].payload
    assert sink.events[0].type == events.TASK_READY
    assert frozenset(payload) == _BASE_KEYS | {"satisfied_dependency_ids"}


def test_task_launched_payload_shape_is_stable() -> None:
    sink = CollectingSink()
    TaskCenterAuditEmitter(sink).task_launched(_TASK, attempt_id="attempt-1")
    payload = sink.events[0].payload
    assert sink.events[0].type == events.TASK_LAUNCHED
    assert frozenset(payload) == _BASE_KEYS


def test_task_failed_payload_shape_is_stable() -> None:
    sink = CollectingSink()
    TaskCenterAuditEmitter(sink).task_failed(
        _TASK,
        attempt_id="attempt-1",
        fail_reason="agent_launch_failed",
        summary="boom",
    )
    payload = sink.events[0].payload
    assert sink.events[0].type == events.TASK_FAILED
    assert frozenset(payload) == _BASE_KEYS | {"fail_reason", "summary"}
