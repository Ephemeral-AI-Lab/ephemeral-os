"""TaskCenter audit event type constants currently emitted by TaskCenter."""

from __future__ import annotations

TASK_READY = "task_center.task.ready"
TASK_LAUNCHED = "task_center.task.launched"
TASK_FAILED = "task_center.task.failed"

__all__ = [
    "TASK_FAILED",
    "TASK_LAUNCHED",
    "TASK_READY",
]
