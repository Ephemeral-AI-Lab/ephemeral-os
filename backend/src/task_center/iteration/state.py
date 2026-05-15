"""Iteration domain DTO, enums, and closure-report DTOs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from task_center.trial.state import TrialFailReason


class IterationStatus(StrEnum):
    OPEN = "open"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class IterationCreationReason(StrEnum):
    INITIAL = "initial"
    PARTIAL_CONTINUATION = "partial_continuation"


@dataclass(frozen=True, slots=True)
class Iteration:
    """Immutable view of a persisted Iteration."""

    id: str
    goal_id: str
    sequence_no: int
    creation_reason: IterationCreationReason
    goal: str
    trial_budget: int
    status: IterationStatus
    trial_ids: tuple[str, ...]
    continuation_goal: str | None
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None
    # Denormalized from the iteration's passing harness trial at close. Both
    # null while open and on failed close.
    task_specification: str | None = None
    task_summary: str | None = None

    @property
    def is_open(self) -> bool:
        return self.status == IterationStatus.OPEN

    @property
    def trial_count(self) -> int:
        # A passing trial closes the iteration immediately, so in practice this
        # equals the number of failed (or startup-failed) trials. Do not
        # rely on that elsewhere.
        return len(self.trial_ids)

    @property
    def has_budget_remaining(self) -> bool:
        return self.trial_count < self.trial_budget

    @property
    def latest_trial_id(self) -> str | None:
        return self.trial_ids[-1] if self.trial_ids else None


@dataclass(frozen=True, slots=True)
class PriorTrialEntry:
    """One past trial's structural state. Phase 06 fills the summary fields."""

    trial_id: str
    trial_sequence_no: int
    task_specification: str | None
    evaluation_criteria: tuple[str, ...]
    fail_reason: TrialFailReason | None
    trial_summary_id: str | None
    failure_landscape: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class TerminalSuccess:
    kind: Literal["terminal_success"] = "terminal_success"


@dataclass(frozen=True, slots=True)
class SuccessContinue:
    goal: str
    kind: Literal["success_continue"] = "success_continue"


@dataclass(frozen=True, slots=True)
class TrialPlanFailed:
    failure_summary: str
    prior_trial_history: tuple[PriorTrialEntry, ...]
    kind: Literal["trial_plan_failed"] = "trial_plan_failed"


ClosureOutcome = TerminalSuccess | SuccessContinue | TrialPlanFailed


@dataclass(frozen=True, slots=True)
class IterationClosureReport:
    iteration_id: str
    final_trial_id: str
    outcome: ClosureOutcome
