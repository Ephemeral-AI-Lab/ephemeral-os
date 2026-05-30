"""TaskCenter task vocabulary: roles, spawn reasons, and statuses.

Describes the persisted task rows (``TaskRow`` in :mod:`task_center._core.persistence`).
Internal vocabulary — not part of the ``task_center`` public facade. The
validated terminal-outcome DTOs live in :mod:`task_center.submissions`.
"""

from __future__ import annotations

from enum import StrEnum


class TaskCenterTaskRole(StrEnum):
    PLANNER = "planner"
    GENERATOR = "generator"
    REDUCER = "reducer"


class TaskCenterTaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_WORKFLOW = "waiting_workflow"
    DONE = "done"
    FAILED = "failed"
    BLOCKED = "blocked"


TERMINAL_GENERATOR_STATUSES: frozenset[TaskCenterTaskStatus] = frozenset(
    {
        TaskCenterTaskStatus.DONE,
        TaskCenterTaskStatus.FAILED,
        TaskCenterTaskStatus.BLOCKED,
    }
)
