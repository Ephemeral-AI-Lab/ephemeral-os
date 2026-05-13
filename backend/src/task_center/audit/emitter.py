"""Emit TaskCenter lifecycle audit events."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from audit.base import AuditEvent, AuditNode, AuditSink, NoopAuditSink
from task_center.audit import events


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
            events.TASK_READY,
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
            events.TASK_LAUNCHED,
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
            events.TASK_FAILED,
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


__all__ = ["TaskCenterAuditEmitter"]
