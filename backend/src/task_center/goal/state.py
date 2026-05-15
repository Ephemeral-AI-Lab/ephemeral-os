"""Goal domain DTO and enums."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal


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
    requested_by_task_id: str
    goal: str
    status: GoalStatus
    iteration_ids: tuple[str, ...]
    final_outcome: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None

    @property
    def is_open(self) -> bool:
        return self.status == GoalStatus.OPEN


@dataclass(frozen=True, slots=True)
class GoalClosureReport:
    """Final report attached to ``requested_by_task_id`` when the goal closes.

    ``final_trial_id`` is normally the passing or final failed trial.
    It remains nullable for defensive compensation paths.
    """

    goal_id: str
    requested_by_task_id: str
    outcome: Literal["success", "failed"]
    final_iteration_id: str
    final_trial_id: str | None

    def to_final_outcome(self) -> dict[str, str | None]:
        return {
            "outcome": self.outcome,
            "final_iteration_id": self.final_iteration_id,
            "final_trial_id": self.final_trial_id,
        }


CloseReportDeliveryStatus = Literal["delivered", "already_delivered"]


@dataclass(frozen=True, slots=True)
class CloseReportDeliveryResult:
    status: CloseReportDeliveryStatus
    requested_by_task_id: str
    parent_trial_id: str | None
