"""Request-scoped task graph orchestrator for the GAN-style executor/planner/evaluator graph.

Public surface:

- :class:`Task`, :class:`Status`, :class:`TaskSummary`, :data:`TaskRole`,
  :data:`TaskId`, :data:`HarnessGraphId` — the data model.
- :class:`TaskCenterHarnessGraph` — decomposition unit.
- :class:`TaskCenterError`, :class:`PlanValidationError` — error hierarchy.
- :func:`compile_dag` — DAG plan validator + dep compiler.
"""

from __future__ import annotations

from task_center.plan import compile_dag
from task_center.errors import PlanValidationError, TaskCenterError
from task_center.harness import TaskCenterHarnessGraph
from task_center.task import (
    HarnessGraphId,
    Status,
    Task,
    TaskId,
    TaskRole,
    TaskSummary,
)

__all__ = [
    "HarnessGraphId",
    "PlanValidationError",
    "Status",
    "Task",
    "TaskCenterError",
    "TaskCenterHarnessGraph",
    "TaskId",
    "TaskRole",
    "TaskSummary",
    "compile_dag",
]
