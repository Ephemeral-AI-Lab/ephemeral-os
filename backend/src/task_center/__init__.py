"""Request-scoped task graph orchestrator for the GAN-style executor/planner/evaluator graph.

Public surface:

- :class:`Task`, :class:`Status`, :class:`TaskSummary`, :data:`TaskRole`,
  :data:`TaskId`, :data:`HarnessGraphId`, :class:`HarnessGraph` — the data model.
- :class:`TaskCenterError`, :class:`PlanValidationError` — error hierarchy.
- :func:`compile_dag` — DAG plan validator + dep compiler.

Subpackages: :mod:`task_center.model`, :mod:`task_center.graph`,
:mod:`task_center.harness_agents`, :mod:`task_center.summaries`,
:mod:`task_center.runtime`.
"""

from __future__ import annotations

from task_center.errors import TaskCenterError
from task_center.graph import PlanValidationError, compile_dag
from task_center.model import (
    HarnessGraph,
    HarnessGraphId,
    Status,
    Task,
    TaskId,
    TaskRole,
    TaskSummary,
)

__all__ = [
    "HarnessGraph",
    "HarnessGraphId",
    "PlanValidationError",
    "Status",
    "Task",
    "TaskCenterError",
    "TaskId",
    "TaskRole",
    "TaskSummary",
    "compile_dag",
]
