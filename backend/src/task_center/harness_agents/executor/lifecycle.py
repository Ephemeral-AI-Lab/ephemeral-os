"""Executor lifecycle operations for TaskCenter."""

from __future__ import annotations

from typing import TYPE_CHECKING

from task_center.errors import TaskCenterError
from task_center.graph import dependency_blocked_descendants
from task_center.model import Status, Task, TaskId, TaskSummary

if TYPE_CHECKING:
    from task_center.runtime.task_center import TaskCenter


def create_root_executor(tc: "TaskCenter", prompt: str) -> Task:
    """Create the root executor task for a user query.

    Uses the ``RunController`` so the root_exec creation goes through the
    same primitive (:meth:`TaskCenter._create_executor`) every other
    executor uses. The root asymmetry (no harness graph, no needs) is
    captured by ``RunController.start``.
    """
    from task_center.runtime.run_controller import RunController

    return RunController(tc=tc).start(prompt)


def submit_task_success(tc: "TaskCenter", task_id: TaskId, summary: str) -> None:
    """Mark an executor task done and notify the enclosing harness graph."""
    task = tc.graph.get(task_id)
    if task.role != "executor":
        raise TaskCenterError(
            f"submit_task_success: task {task_id!r} role {task.role!r} not allowed"
        )
    task.summaries.append(
        TaskSummary(kind="success", text=summary, source_task_id=task_id)
    )
    tc._mark_terminal(task, Status.DONE)
    tc._notify_child_terminal_changed()
    tc._persist_all()
    tc._wakeup.set()


def submit_task_failure(tc: "TaskCenter", task_id: TaskId, summary: str) -> None:
    """Mark an executor failed and fail dependency-blocked descendants."""
    task = tc.graph.get(task_id)
    if task.role != "executor":
        raise TaskCenterError(
            f"submit_task_failure: task {task_id!r} role {task.role!r} is not executor"
        )
    task.summaries.append(
        TaskSummary(kind="failure", text=summary, source_task_id=task_id)
    )
    tc._mark_terminal(task, Status.FAILED)
    for descendant in dependency_blocked_descendants(tc.graph, task_id):
        descendant.summaries.append(
            TaskSummary(
                kind="dependency_blocked",
                text=f"Blocked because dependency {task_id!r} failed.",
                source_task_id=task_id,
            )
        )
        tc._mark_terminal(descendant, Status.FAILED)
    tc._notify_child_terminal_changed()
    tc._persist_all()
    tc._wakeup.set()


def handle_silent_termination(tc: "TaskCenter", task: Task, reason: str) -> None:
    """Treat a silent executor exit as a scoped task failure."""
    submit_task_failure(tc, task.id, reason)
