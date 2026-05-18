"""Iteration domain DTO, enums, and closure-report DTOs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from task_center.attempt.state import AttemptFailReason


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
    attempt_budget: int
    status: IterationStatus
    attempt_ids: tuple[str, ...]
    next_iteration_handoff_goal: str | None
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None
    # Denormalized from the iteration's passing harness attempt at close. Both
    # null while open and on failed close.
    plan_spec: str | None = None
    task_summary: str | None = None

    @property
    def is_open(self) -> bool:
        return self.status == IterationStatus.OPEN

    @property
    def attempt_count(self) -> int:
        # A passing attempt closes the iteration immediately, so in practice this
        # equals the number of failed (or startup-failed) attempts. Do not
        # rely on that elsewhere.
        return len(self.attempt_ids)

    @property
    def has_budget_remaining(self) -> bool:
        return self.attempt_count < self.attempt_budget

    @property
    def latest_attempt_id(self) -> str | None:
        return self.attempt_ids[-1] if self.attempt_ids else None


@dataclass(frozen=True, slots=True)
class PriorAttemptEntry:
    """One past attempt's structural state. Phase 06 fills the summary fields."""

    attempt_id: str
    attempt_sequence_no: int
    plan_spec: str | None
    evaluation_criteria: tuple[str, ...]
    fail_reason: AttemptFailReason | None
    attempt_summary_id: str | None
    failure_landscape: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class TerminalSuccess:
    kind: Literal["terminal_success"] = "terminal_success"


@dataclass(frozen=True, slots=True)
class SuccessContinue:
    goal: str
    kind: Literal["success_continue"] = "success_continue"


@dataclass(frozen=True, slots=True)
class AttemptPlanFailed:
    failure_summary: str
    prior_attempt_history: tuple[PriorAttemptEntry, ...]
    kind: Literal["attempt_plan_failed"] = "attempt_plan_failed"


ClosureOutcome = TerminalSuccess | SuccessContinue | AttemptPlanFailed


@dataclass(frozen=True, slots=True)
class IterationClosureReport:
    iteration_id: str
    final_attempt_id: str
    outcome: ClosureOutcome
