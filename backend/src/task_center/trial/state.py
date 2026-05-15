"""Trial domain DTO and enums."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class TrialStage(StrEnum):
    PLAN = "plan"
    GENERATE = "generate"
    EVALUATE = "evaluate"
    CLOSED = "closed"


class TrialStatus(StrEnum):
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"


class TrialFailReason(StrEnum):
    PLANNER_FAILED = "planner_failed"
    GENERATOR_FAILED = "generator_failed"
    EVALUATOR_FAILED = "evaluator_failed"
    STARTUP_FAILED = "startup_failed"


@dataclass(frozen=True, slots=True)
class Trial:
    """Immutable view of a persisted Trial."""

    id: str
    iteration_id: str
    trial_sequence_no: int
    stage: TrialStage
    status: TrialStatus
    planner_task_id: str | None
    task_specification: str | None
    evaluation_criteria: tuple[str, ...]
    generator_task_ids: tuple[str, ...]
    evaluator_task_id: str | None
    continuation_goal: str | None
    fail_reason: TrialFailReason | None
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None

    @property
    def is_closed(self) -> bool:
        return self.stage == TrialStage.CLOSED

    @property
    def has_partial_continuation(self) -> bool:
        return self.continuation_goal is not None
