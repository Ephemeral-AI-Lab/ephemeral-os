"""Consolidated TaskCenter domain DTOs and enums.

The durable model is Workflow -> Iteration -> Attempt. This single module owns
all three frozen DTOs and their lifecycle enums (previously split across
``workflow/state.py``, ``iteration/state.py``, ``attempt/state.py``). Leaf
module — depends only on stdlib, so the persistence protocols, outcomes
algebra, invariants, and the three coordinators can all import it without a
cycle.

There is no ``*ClosureReport`` / ``WorkflowOrigin`` abstraction: outcomes +
status propagate up, and the single child-workflow resolution drives the
lifecycle. ``close`` is a state transition, not a report.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any


# ---- Workflow (origin axis) ------------------------------------------------


class WorkflowStatus(StrEnum):
    OPEN = "open"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class Workflow:
    """Immutable view of a persisted Workflow.

    Every workflow is generator-spawned (the root via a synthetic run-level
    bootstrap generator task), so ``parent_task_id`` is the backward link to
    the spawning task — bidirectional with ``Task.child_workflow_id``.
    """

    id: str
    task_center_run_id: str
    workflow_goal: str
    status: WorkflowStatus
    iteration_ids: tuple[str, ...]
    parent_task_id: str | None
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None

    @property
    def is_open(self) -> bool:
        return self.status == WorkflowStatus.OPEN


# ---- Iteration (vertical continuation axis) --------------------------------


class IterationStatus(StrEnum):
    OPEN = "open"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class IterationCreationReason(StrEnum):
    INITIAL = "initial"
    DEFERRED_GOAL_CONTINUATION = "deferred_goal_continuation"


@dataclass(frozen=True, slots=True)
class Iteration:
    """Immutable view of a persisted Iteration.

    ``outcomes`` is the persisted, canonical projection (a ``json.dumps`` list
    of execution outcome records): the passing attempt's reducer outcomes, or
    when the iteration failed, its last failed attempt's failed-task outcomes.
    ``None`` while open.
    """

    id: str
    workflow_id: str
    sequence_no: int
    creation_reason: IterationCreationReason
    iteration_goal: str
    attempt_budget: int
    status: IterationStatus
    attempt_ids: tuple[str, ...]
    deferred_goal_for_next_iteration: str | None
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None
    outcomes: str | None = None

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


# ---- Attempt (horizontal retry axis) ---------------------------------------


class AttemptStage(StrEnum):
    PLAN = "plan"
    RUN = "run"
    CLOSED = "closed"


class AttemptStatus(StrEnum):
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"


class AttemptFailReason(StrEnum):
    TASK_FAILED = "task_failed"
    STARTUP_FAILED = "startup_failed"


@dataclass(frozen=True, slots=True)
class Attempt:
    """Immutable view of a persisted Attempt.

    An attempt is a planner-authored plan: a DAG of generator + reducer tasks.
    The two task-id tuples are the plan; the single RUN stage schedules their
    union to quiescence.
    """

    id: str
    iteration_id: str
    attempt_sequence_no: int
    stage: AttemptStage
    status: AttemptStatus
    planner_task_id: str | None
    generator_task_ids: tuple[str, ...]
    reducer_task_ids: tuple[str, ...]
    deferred_goal_for_next_iteration: str | None
    fail_reason: AttemptFailReason | None
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None
    outcomes: tuple[Any, ...] = ()

    @property
    def is_closed(self) -> bool:
        return self.stage == AttemptStage.CLOSED


__all__ = [
    "Attempt",
    "AttemptFailReason",
    "AttemptStage",
    "AttemptStatus",
    "Iteration",
    "IterationCreationReason",
    "IterationStatus",
    "Workflow",
    "WorkflowStatus",
]
