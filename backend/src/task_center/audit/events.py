"""TaskCenter audit event types and typed payload schemas.

Audit events were previously three bare-string module constants. This
module exposes:

- :class:`TaskCenterAuditEventType` — typed StrEnum of every event the
  package emits.
- Typed payload dataclasses (one per event type) so that schema evolution
  is visible at the type level instead of relying on dict shape discipline
  across emitter + consumer.

The legacy ``TASK_READY``/``TASK_LAUNCHED``/``TASK_FAILED`` string
constants are retained as enum-value aliases so external sinks can still
match on string equality.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


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


__all__ = [
    "TaskCenterAuditEventType",
    "TASK_FAILED",
    "TASK_LAUNCHED",
    "TASK_READY",
    "TaskFailedPayload",
    "TaskLaunchedPayload",
    "TaskReadyPayload",
]
