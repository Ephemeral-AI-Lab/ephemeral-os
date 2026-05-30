"""TaskCenter package primitives — invariant exception, task-id helpers, lifecycle config.

Persistence I/O Protocols live in :mod:`task_center._core.persistence`.
"""

from __future__ import annotations

from dataclasses import dataclass


# ---- Exceptions ------------------------------------------------------------


class TaskCenterInvariantViolation(Exception):
    """Raised when a harness lifecycle invariant is violated.

    Hard, non-tolerable harness state breach.
    """


# ---- Stable task ids -------------------------------------------------------


def planner_task_id(attempt_id: str) -> str:
    return f"{attempt_id}:planner"


def generator_task_id(attempt_id: str, local_task_id: str) -> str:
    return f"{attempt_id}:gen:{local_task_id}"


def reducer_task_id(attempt_id: str, local_task_id: str) -> str:
    return f"{attempt_id}:red:{local_task_id}"


def root_task_id(run_id: str) -> str:
    return f"{run_id}:root"


def attempt_id_from_task_id(task_id: str) -> str | None:
    """Return the attempt id encoded in a plan task id, or ``None``.

    Plan tasks are ``<attempt_id>:planner`` / ``<attempt_id>:gen:<local>`` /
    ``<attempt_id>:red:<local>``; the run-level bootstrap task is
    ``<run_id>:root`` (no attempt) and yields ``None``.
    """
    for sep in (":gen:", ":red:"):
        if sep in task_id:
            return task_id.split(sep, 1)[0]
    if task_id.endswith(":planner"):
        return task_id[: -len(":planner")]
    return None


# ---- Runtime configuration -------------------------------------------------


@dataclass(frozen=True, slots=True)
class TaskCenterLifecycleConfig:
    """Configurable knobs for the workflow/iteration/attempt lifecycle.

    ``default_attempt_budget`` is applied to every Iteration created by
    ``WorkflowLifecycle`` unless overridden per-call.
    """

    default_attempt_budget: int = 2


__all__ = [
    "TaskCenterInvariantViolation",
    "TaskCenterLifecycleConfig",
    "attempt_id_from_task_id",
    "generator_task_id",
    "planner_task_id",
    "reducer_task_id",
    "root_task_id",
]
