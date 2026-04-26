"""Plan-validation errors raised by ``compile_dag``."""

from __future__ import annotations

from task_center.errors import TaskCenterError


class PlanValidationError(TaskCenterError):
    """Raised by ``compile_dag`` when an executor's submitted plan fails validation."""
