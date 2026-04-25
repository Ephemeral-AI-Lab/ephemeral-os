"""Exception types raised by the task_center module."""

from __future__ import annotations


class TaskCenterError(Exception):
    """Base class for all task_center errors."""


class PhaseValidationError(TaskCenterError):
    """Raised by ``compile_phases`` when an executor's submitted phased plan
    fails validation.
    """
