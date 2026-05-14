"""TaskCenter audit event types, typed payload schemas, and emitter.

Audit events were previously three bare-string module constants split
across a sub-package. This module exposes:

- :class:`TaskCenterAuditEventType` — typed StrEnum of every event the
  package emits.
- Typed payload dataclasses (one per event type) so that schema evolution
  is visible at the type level instead of relying on dict shape discipline.
- :class:`TaskCenterAuditEmitter` — small write-only facade around a
  shared audit sink that handles task-node + payload assembly.

The legacy ``TASK_READY``/``TASK_LAUNCHED``/``TASK_FAILED`` string
constants are retained as enum-value aliases so external sinks can still
match on string equality.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from audit.base import AuditEvent, AuditNode, AuditSink, NoopAuditSink


class TaskCenterAuditEventType(StrEnum):
    """Every audit event type the TaskCenter package emits."""

    TASK_READY = "task_center.task.ready"
    TASK_LAUNCHED = "task_center.task.launched"
    TASK_FAILED = "task_center.task.failed"


# Backwards-compatible string aliases. Prefer the enum at new call sites.
TASK_READY: str = TaskCenterAuditEventType.TASK_READY.value
TASK_LAUNCHED: str = TaskCenterAuditEventType.TASK_LAUNCHED.value
TASK_FAILED: str = TaskCenterAuditEventType.TASK_FAILED.value


@dataclass(frozen=True, slots=True)
class _BaseTaskPayload:
    run_id: str | None
    attempt_id: str | None
    task_center_task_id: str | None
    role: str | None
    agent_name: str | None
    needs: tuple[str, ...] = ()
    context_packet_id: str | None = None
    status_from: str = ""
    status_to: str = ""


@dataclass(frozen=True, slots=True)
class TaskReadyPayload(_BaseTaskPayload):
    """Payload schema for ``task_center.task.ready`` events."""

    satisfied_dependency_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TaskLaunchedPayload(_BaseTaskPayload):
    """Payload schema for ``task_center.task.launched`` events."""


@dataclass(frozen=True, slots=True)
class TaskFailedPayload(_BaseTaskPayload):
    """Payload schema for ``task_center.task.failed`` events."""

    fail_reason: str | None = None
    summary: str | None = None


class TaskCenterAuditEmitter:
    """Small write-only facade around a shared audit sink."""

    def __init__(self, sink: AuditSink | None = None) -> None:
        self._sink = sink if sink is not None else NoopAuditSink()

    def publish(
        self,
        event_type: str,
        *,
        node: AuditNode,
        payload: Mapping[str, Any] | None = None,
    ) -> None:
        self._sink.publish(
            AuditEvent(
                source="task_center",
                type=event_type,
                node=node,
                payload=dict(payload or {}),
            )
        )

    def task_ready(
        self,
        task: Mapping[str, Any],
        *,
        attempt_id: str | None,
        satisfied_dependency_ids: Sequence[str],
    ) -> None:
        """Emit dispatcher-owned readiness without implying a status mutation."""
        self.publish(
            TASK_READY,
            node=_task_node(task, attempt_id=attempt_id),
            payload={
                **_task_payload(task),
                "status_from": "pending",
                "status_to": "pending",
                "satisfied_dependency_ids": [
                    str(dep) for dep in satisfied_dependency_ids
                ],
            },
        )

    def task_launched(
        self,
        task: Mapping[str, Any],
        *,
        attempt_id: str | None,
        status_from: str = "pending",
    ) -> None:
        self.publish(
            TASK_LAUNCHED,
            node=_task_node(task, attempt_id=attempt_id),
            payload={
                **_task_payload(task),
                "status_from": status_from,
                "status_to": str(task.get("status") or "running"),
            },
        )

    def task_failed(
        self,
        task: Mapping[str, Any],
        *,
        attempt_id: str | None,
        status_from: str = "running",
        fail_reason: str = "",
        summary: str = "",
    ) -> None:
        self.publish(
            TASK_FAILED,
            node=_task_node(task, attempt_id=attempt_id),
            payload={
                **_task_payload(task),
                "status_from": status_from,
                "status_to": str(task.get("status") or "failed"),
                "fail_reason": fail_reason or None,
                "summary": summary or None,
            },
        )


def _task_node(
    task: Mapping[str, Any],
    *,
    attempt_id: str | None,
) -> AuditNode:
    return AuditNode(
        task_center_run_id=_text(task.get("task_center_run_id")),
        attempt_id=_text(attempt_id or task.get("task_center_attempt_id")),
        task_center_task_id=_text(task.get("id")),
        agent_name=_text(task.get("agent_name")),
    )


def _task_payload(task: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "run_id": _text(task.get("task_center_run_id")),
        "attempt_id": _text(task.get("task_center_attempt_id")),
        "task_center_task_id": _text(task.get("id")),
        "role": _text(task.get("role")),
        "agent_name": _text(task.get("agent_name")),
        "needs": [str(dep) for dep in task.get("needs", ()) or ()],
        "context_packet_id": _text(task.get("context_packet_id")),
    }


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = [
    "TASK_FAILED",
    "TASK_LAUNCHED",
    "TASK_READY",
    "TaskCenterAuditEmitter",
    "TaskCenterAuditEventType",
    "TaskFailedPayload",
    "TaskLaunchedPayload",
    "TaskReadyPayload",
]
