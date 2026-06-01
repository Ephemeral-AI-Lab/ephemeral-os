"""Validated terminal-outcome submission DTOs (tools ↔ workflow contract)."""

from __future__ import annotations

from typing import Any, Literal
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PlannerSubmission:
    """Validated planner submission from a full or partial plan tool."""

    attempt_id: str
    planner_task_id: str
    kind: Literal["completes", "defers"]
    generator_task_ids: tuple[str, ...]
    reducer_task_ids: tuple[str, ...]
    deferred_goal_for_next_iteration: str | None


@dataclass(frozen=True, slots=True)
class PlannerFailureSubmission:
    """Runtime-synthesized planner failure."""

    attempt_id: str
    planner_task_id: str
    fail_reason: Literal["run_exhausted"]


@dataclass(frozen=True, slots=True)
class GeneratorSubmission:
    """Validated terminal outcome for one generator task."""

    attempt_id: str
    task_id: str
    status: Literal["success", "failed"]
    outcome: str
    terminal_tool_result: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ReducerSubmission:
    """Validated terminal outcome for one reducer task (binary)."""

    attempt_id: str
    task_id: str
    status: Literal["success", "failed"]
    outcome: str
    terminal_tool_result: dict[str, Any]
