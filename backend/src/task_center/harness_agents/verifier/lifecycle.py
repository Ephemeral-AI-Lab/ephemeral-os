"""Verifier lifecycle operations for TaskCenter.

Stage 2 of the four-role roadmap lands a *degraded* recovery surface:
``submit_verification_success`` unblocks dependents the same way executor
success does, but ``submit_verification_failure`` cascade-fails dependents
instead of triggering a fix-executor. The full recovery (FIXING → fix-executor
→ verifier re-run) lands with Stage 6.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from task_center.errors import TaskCenterError
from task_center.graph import dependency_blocked_descendants
from task_center.model import Status, TaskId, TaskSummary

if TYPE_CHECKING:
    from task_center.runtime.task_center import TaskCenter


def submit_verification_success(
    tc: "TaskCenter", task_id: TaskId, summary: str
) -> None:
    """Mark a verifier DONE; dependents promote on the next dispatcher tick."""
    task = tc.graph.get(task_id)
    if task.role != "verifier":
        raise TaskCenterError(
            f"submit_verification_success: task {task_id!r} role "
            f"{task.role!r} is not verifier"
        )
    task.summaries.append(
        TaskSummary(kind="success", text=summary, source_task_id=task_id)
    )
    tc._mark_terminal(task, Status.DONE)
    tc._notify_child_terminal_changed()
    tc._persist_all()
    tc._wakeup.set()


def submit_verification_failure(
    tc: "TaskCenter", task_id: TaskId, summary: str
) -> None:
    """Stage 2 — degraded path: mark verifier FAILED and cascade-fail dependents.

    Stage 6 replaces the cascade-fail with the FIXING → fix-executor flow.
    """
    task = tc.graph.get(task_id)
    if task.role != "verifier":
        raise TaskCenterError(
            f"submit_verification_failure: task {task_id!r} role "
            f"{task.role!r} is not verifier"
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


def handle_silent_termination(tc: "TaskCenter", task, reason: str) -> None:
    """Treat a silent verifier exit as a verification failure."""
    submit_verification_failure(tc, task.id, reason)
