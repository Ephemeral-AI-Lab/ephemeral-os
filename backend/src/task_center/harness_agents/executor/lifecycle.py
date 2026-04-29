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
    """Mark an executor task done and notify the enclosing harness graph.

    Stage 6: if this executor was spawned with ``spawn_reason='fix_verification'``
    (a fix-executor), success means the fix landed — the verifier re-runs.
    """
    task = tc.graph.get(task_id)
    if task.role != "executor":
        raise TaskCenterError(
            f"submit_task_success: task {task_id!r} role {task.role!r} not allowed"
        )
    task.summaries.append(
        TaskSummary(kind="success", text=summary, source_task_id=task_id)
    )
    tc._mark_terminal(task, Status.DONE)

    if (
        task.spawn_reason == "fix_verification"
        and task.fix_target_id is not None
    ):
        # Lazy import — verifier_lifecycle imports back into the runtime.
        from task_center.harness_agents.verifier import lifecycle as verifier_lifecycle

        verifier_lifecycle.reenter_after_fix_success(
            tc, task.fix_target_id, task.id, summary
        )

    tc._notify_child_terminal_changed()
    tc._persist_all()
    tc._wakeup.set()


def submit_task_failure(tc: "TaskCenter", task_id: TaskId, summary: str) -> None:
    """Mark an executor failed and fail dependency-blocked descendants.

    Stage 6: if this executor was a fix-executor, failure escalates to the
    verifier — the verifier transitions to FAILED and ITS dependents
    cascade-fail (the fix-executor's "siblings" are the verifier's deps).
    """
    task = tc.graph.get(task_id)
    if task.role != "executor":
        raise TaskCenterError(
            f"submit_task_failure: task {task_id!r} role {task.role!r} is not executor"
        )
    task.summaries.append(
        TaskSummary(kind="failure", text=summary, source_task_id=task_id)
    )
    tc._mark_terminal(task, Status.FAILED)

    if (
        task.spawn_reason == "fix_verification"
        and task.fix_target_id is not None
    ):
        from task_center.harness_agents.verifier import lifecycle as verifier_lifecycle

        verifier_lifecycle.fail_after_fix_failure(
            tc, task.fix_target_id, summary
        )
    else:
        for descendant in dependency_blocked_descendants(tc.graph, task_id):
            descendant.summaries.append(
                TaskSummary(
                    kind="dependency_blocked",
                    text=f"Blocked because dependency {task_id!r} failed.",
                    source_task_id=task_id,
                )
            )
            tc._mark_terminal(descendant, Status.FAILED)
        if task.task_center_harness_graph_id is not None:
            from task_center.runtime.closure import close_if_terminal_verifier_failed

            close_if_terminal_verifier_failed(tc, task.task_center_harness_graph_id)

    tc._notify_child_terminal_changed()
    tc._persist_all()
    tc._wakeup.set()


def handle_silent_termination(tc: "TaskCenter", task: Task, reason: str) -> None:
    """Treat a silent executor exit as a scoped task failure."""
    submit_task_failure(tc, task.id, reason)
