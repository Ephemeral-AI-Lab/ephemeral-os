"""TaskCenter attempt task roles, statuses, and submission DTOs."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal


class TaskCenterTaskRole(StrEnum):
    PLANNER = "planner"
    GENERATOR = "generator"
    EVALUATOR = "evaluator"
    ENTRY_EXECUTOR = "entry_executor"


class SpawnReason(StrEnum):
    """Why a task row was created. Replaces free-form spawn_reason strings."""

    ATTEMPT_PLANNER = "attempt_planner"
    ATTEMPT_GENERATOR = "attempt_generator"
    ATTEMPT_EVALUATOR = "attempt_evaluator"
    ENTRY_EXECUTOR = "entry_executor"


class TaskCenterTaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_GOAL = "waiting_goal"
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


@dataclass(frozen=True, slots=True)
class PlannedGeneratorTask:
    """One normalized generator DAG node."""

    local_id: str
    agent_name: str
    deps: tuple[str, ...]
    task_spec: str


@dataclass(frozen=True, slots=True)
class PlannerSubmission:
    """Validated planner submission from a full or partial plan tool."""

    attempt_id: str
    planner_task_id: str
    kind: Literal["completes", "defers"]
    plan_spec: str
    evaluation_criteria: tuple[str, ...]
    tasks: tuple[PlannedGeneratorTask, ...]
    deferred_goal_for_next_iteration: str | None
    summary: str


@dataclass(frozen=True, slots=True)
class PlannerFailureSubmission:
    """Runtime-synthesized planner failure."""

    attempt_id: str
    planner_task_id: str
    fail_reason: Literal["run_exhausted"]
    summary: str


@dataclass(frozen=True, slots=True)
class GeneratorSubmission:
    """Validated terminal outcome for one generator task."""

    attempt_id: str
    task_id: str
    outcome: Literal["success", "failure"]
    summary: str
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class EvaluatorSubmission:
    """Validated terminal outcome for one evaluator task."""

    attempt_id: str
    task_id: str
    outcome: Literal["success", "failure"]
    summary: str
    payload: dict[str, Any]
