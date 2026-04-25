"""Per-session task graph orchestrator for the phased executor-evaluator tree.

Public surface:

- :class:`Task`, :class:`Status`, :data:`TaskRole`, :data:`TaskId`,
  :data:`SubtreeKind` — the data model.
- :class:`TaskCenterError`, :class:`PhaseValidationError` — error hierarchy.
- :func:`compile_phases` — phase plan validator + dep compiler.

Higher-level types (``TaskGraph``, ``TaskCenter``) will be re-exported here
as the corresponding modules land.
"""

from __future__ import annotations

from task_center.errors import PhaseValidationError, TaskCenterError
from task_center.phases import compile_phases
from task_center.task import (
    Status,
    SubtreeKind,
    Task,
    TaskId,
    TaskRole,
)

__all__ = [
    "PhaseValidationError",
    "Status",
    "SubtreeKind",
    "Task",
    "TaskCenterError",
    "TaskId",
    "TaskRole",
    "compile_phases",
]
