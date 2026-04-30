"""Harness graph task roles, statuses, and submission DTOs."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal


class HarnessTaskRole(StrEnum):
    PLANNER = "planner"
    GENERATOR = "generator"
    EVALUATOR = "evaluator"


class HarnessTaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_COMPLEX_TASK = "waiting_complex_task"
    DONE = "done"
    FAILED = "failed"
    BLOCKED = "blocked"


TERMINAL_GENERATOR_STATUSES: frozenset[HarnessTaskStatus] = frozenset(
    {
        HarnessTaskStatus.DONE,
        HarnessTaskStatus.FAILED,
        HarnessTaskStatus.BLOCKED,
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

    graph_id: str
    planner_task_id: str
    kind: Literal["full", "partial"]
    task_specification: str
    evaluation_criteria: tuple[str, ...]
    tasks: tuple[PlannedGeneratorTask, ...]
    continuation_goal: str | None
    summary: str


@dataclass(frozen=True, slots=True)
class PlannerFailureSubmission:
    """Runtime-synthesized planner failure."""

    graph_id: str
    planner_task_id: str
    fail_reason: Literal["run_exhausted"]
    summary: str


@dataclass(frozen=True, slots=True)
class GeneratorSubmission:
    """Validated terminal outcome for one generator task."""

    graph_id: str
    task_id: str
    outcome: Literal["success", "failure"]
    summary: str
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class EvaluatorSubmission:
    """Validated terminal outcome for one evaluator task."""

    graph_id: str
    task_id: str
    outcome: Literal["success", "failure"]
    summary: str
    payload: dict[str, Any]
