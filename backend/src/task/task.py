"""First-class persisted task DTO and status vocabulary."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from agents import AgentRole


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    BLOCKED = "blocked"


TASK_AGENT_ROLES = frozenset(
    {
        AgentRole.ROOT,
        AgentRole.PLANNER,
        AgentRole.GENERATOR,
        AgentRole.REDUCER,
    }
)

TERMINAL_GENERATOR_STATUSES: frozenset[TaskStatus] = frozenset(
    {
        TaskStatus.DONE,
        TaskStatus.FAILED,
        TaskStatus.BLOCKED,
    }
)


@dataclass(frozen=True, slots=True)
class Task:
    id: str
    request_id: str
    role: AgentRole
    instruction: str
    status: TaskStatus
    workflow_id: str | None = None
    iteration_id: str | None = None
    attempt_id: str | None = None
    agent_name: str | None = None
    needs: tuple[str, ...] = ()
    outcomes: tuple[Any, ...] = ()
    terminal_tool_result: dict[str, Any] | None = None


__all__ = [
    "TASK_AGENT_ROLES",
    "TERMINAL_GENERATOR_STATUSES",
    "Task",
    "TaskStatus",
]
