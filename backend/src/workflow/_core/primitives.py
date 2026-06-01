"""Workflow package primitives: invariant exception, task-id builders, lifecycle config.

Persistence I/O Protocols live in :mod:`workflow._core.persistence`.
"""

from __future__ import annotations

from dataclasses import dataclass


# ---- Exceptions ------------------------------------------------------------


class WorkflowInvariantViolation(Exception):
    """Raised when a workflow lifecycle invariant is violated.

    Hard, non-tolerable workflow state breach.
    """


# ---- Stable task ids -------------------------------------------------------


def planner_task_id(attempt_id: str) -> str:
    return f"{attempt_id}:planner"


def generator_task_id(attempt_id: str, local_task_id: str) -> str:
    return f"{attempt_id}:gen:{local_task_id}"


def reducer_task_id(attempt_id: str, local_task_id: str) -> str:
    return f"{attempt_id}:red:{local_task_id}"


# ---- Runtime configuration -------------------------------------------------


@dataclass(frozen=True, slots=True)
class WorkflowLifecycleConfig:
    """Configurable knobs for the workflow/iteration/attempt lifecycle.

    ``default_attempt_budget`` is applied to every Iteration created by
    ``WorkflowLifecycle`` unless overridden per-call.
    """

    default_attempt_budget: int = 2


__all__ = [
    "WorkflowInvariantViolation",
    "WorkflowLifecycleConfig",
    "generator_task_id",
    "planner_task_id",
    "reducer_task_id",
]
