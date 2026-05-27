"""Goal domain DTO and enums."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal


class GoalOriginKind(StrEnum):
    ENTRY = "entry"
    TASK = "task"


@dataclass(frozen=True, slots=True)
class GoalOrigin:
    """Where prompt text entered the goal lifecycle."""

    kind: GoalOriginKind
    task_center_run_id: str | None = None
    task_id: str | None = None

    @classmethod
    def entry(cls, *, task_center_run_id: str) -> "GoalOrigin":
        return cls(kind=GoalOriginKind.ENTRY, task_center_run_id=task_center_run_id)

    @classmethod
    def task(cls, *, task_id: str) -> "GoalOrigin":
        return cls(kind=GoalOriginKind.TASK, task_id=task_id)

    def __post_init__(self) -> None:
        if self.kind == GoalOriginKind.ENTRY:
            if not self.task_center_run_id or self.task_id is not None:
                raise ValueError("entry goal origin requires only task_center_run_id")
            return
        if self.kind == GoalOriginKind.TASK:
            if not self.task_id or self.task_center_run_id is not None:
                raise ValueError("task goal origin requires only task_id")
            return
        raise ValueError(f"Unsupported goal origin kind: {self.kind!r}")


class GoalStatus(StrEnum):
    OPEN = "open"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class Goal:
    """Immutable view of a persisted Goal."""

    id: str
    task_center_run_id: str
    goal: str
    status: GoalStatus
    iteration_ids: tuple[str, ...]
    final_outcome: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None
    origin_kind: GoalOriginKind = GoalOriginKind.TASK
    requested_by_task_id: str | None = None

    @property
    def is_open(self) -> bool:
        return self.status == GoalStatus.OPEN

    @property
    def origin(self) -> GoalOrigin:
        if self.origin_kind == GoalOriginKind.ENTRY:
            return GoalOrigin.entry(task_center_run_id=self.task_center_run_id)
        if self.requested_by_task_id is None:
            raise ValueError("task-origin goal is missing requested_by_task_id")
        return GoalOrigin.task(task_id=self.requested_by_task_id)


@dataclass(frozen=True, slots=True)
class GoalClosureReport:
    """Final report emitted when a goal closes.

    ``final_attempt_id`` is normally the passing or final failed attempt.
    It remains nullable for defensive compensation paths.
    """

    goal_id: str
    task_center_run_id: str
    origin_kind: GoalOriginKind
    requested_by_task_id: str | None
    outcome: Literal["success", "failed"]
    final_iteration_id: str
    final_attempt_id: str | None

    def to_final_outcome(self) -> dict[str, str | None]:
        return {
            "outcome": self.outcome,
            "final_iteration_id": self.final_iteration_id,
            "final_attempt_id": self.final_attempt_id,
        }


GoalClosureDeliveryStatus = Literal["delivered", "already_delivered"]


@dataclass(frozen=True, slots=True)
class GoalClosureDeliveryResult:
    status: GoalClosureDeliveryStatus
    requested_by_task_id: str | None
    parent_attempt_id: str | None
