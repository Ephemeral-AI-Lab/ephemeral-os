"""Graph validation errors."""

from __future__ import annotations

from task_center.errors import TaskCenterError


class PlanValidationError(TaskCenterError):
    """Raised when a submitted executor DAG plan fails validation."""
