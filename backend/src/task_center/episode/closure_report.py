"""EpisodeClosureReport — closure signal from manager to handler."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from task_center.attempt import AttemptFailReason


@dataclass(frozen=True, slots=True)
class AttemptedPlanEntry:
    """One past attempt's structural state. Phase 06 fills the summary fields."""

    attempt_id: str
    attempt_sequence_no: int
    task_specification: str | None
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
    attempted_plan_history: tuple[AttemptedPlanEntry, ...]
    kind: Literal["attempt_plan_failed"] = "attempt_plan_failed"


ClosureOutcome = TerminalSuccess | SuccessContinue | AttemptPlanFailed


@dataclass(frozen=True, slots=True)
class EpisodeClosureReport:
    episode_id: str
    final_attempt_id: str
    outcome: ClosureOutcome
