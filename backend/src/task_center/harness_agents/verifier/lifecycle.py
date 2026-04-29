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
    """Mark a verifier DONE and close the graph if it is the final verifier."""
    from task_center.runtime.closure import (
        close_harness_graph_success,
        is_terminal_verifier,
    )
    from task_center.runtime.orchestrator import Orchestrator

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
    assert task.task_center_harness_graph_id is not None
    graph = tc.graph.get_harness_graph(task.task_center_harness_graph_id)
    if is_terminal_verifier(tc, graph.id, task.id):
        if graph.plan_shape == "partial":
            Orchestrator(graph_id=graph.id, tc=tc).close_partial_success(
                summary, source_task_id=task.id
            )
        else:
            close_harness_graph_success(tc, graph.id, task.id)
    tc._notify_child_terminal_changed()
    tc._persist_all()
    tc._wakeup.set()


def submit_verification_failure(
    tc: "TaskCenter", task_id: TaskId, summary: str
) -> None:
    """Stage 6 — verifier failure transitions to FIXING + spawns fix-executor.

    The verifier stays in ``FIXING`` until its fix-executor reports back.
    On fix success the verifier transitions back to READY (re-runs); on
    fix failure the verifier transitions to FAILED and dependents
    cascade-fail through the existing Stage 2 path.

    The verifier must be in a non-terminal state at call time (typically
    RUNNING — the dispatcher transitioned it before the agent invoked the
    terminal). FIXING is the intermediate state.
    """
    from task_center.runtime.orchestrator import Orchestrator

    task = tc.graph.get(task_id)
    if task.role != "verifier":
        raise TaskCenterError(
            f"submit_verification_failure: task {task_id!r} role "
            f"{task.role!r} is not verifier"
        )
    task.summaries.append(
        TaskSummary(kind="failure", text=summary, source_task_id=task_id)
    )
    tc.graph.transition(task.id, Status.FIXING)
    assert task.task_center_harness_graph_id is not None
    Orchestrator(
        graph_id=task.task_center_harness_graph_id, tc=tc
    ).create_harness_fix_executor(task.id, summary)
    tc._notify_child_terminal_changed()
    tc._persist_all()
    tc._wakeup.set()


def reenter_after_fix_success(
    tc: "TaskCenter", verifier_id: TaskId, fix_executor_id: TaskId, fix_summary: str
) -> None:
    """Stage 6 — fix-executor reported success; re-run the verifier.

    Transitions the verifier from FIXING back to READY so the dispatcher
    re-spawns it, and attaches the fix-executor's success summary onto
    the verifier as a ``child_success`` entry. The verifier's prior
    failure summary stays in place so the re-running agent can read both
    the original deficiency and what the fix did.
    """
    verifier = tc.graph.get(verifier_id)
    if verifier.status is not Status.FIXING:
        raise TaskCenterError(
            f"reenter_after_fix_success: verifier {verifier_id!r} is in "
            f"status {verifier.status.value!r}, expected 'fixing'"
        )
    verifier.summaries.append(
        TaskSummary(
            kind="child_success",
            text=f"Fix-executor applied: {fix_summary}",
            source_task_id=fix_executor_id,
        )
    )
    tc.graph.transition(verifier.id, Status.READY)


def fail_after_fix_failure(
    tc: "TaskCenter", verifier_id: TaskId, fix_failure_summary: str
) -> None:
    """Stage 6 — fix-executor failed; the verifier itself FAILS now.

    Cascade-fails dependency-blocked descendants the same way Stage 2's
    degraded path did.
    """
    from task_center.runtime.closure import close_if_terminal_verifier_failed

    verifier = tc.graph.get(verifier_id)
    if verifier.status is not Status.FIXING:
        raise TaskCenterError(
            f"fail_after_fix_failure: verifier {verifier_id!r} is in "
            f"status {verifier.status.value!r}, expected 'fixing'"
        )
    verifier.summaries.append(
        TaskSummary(
            kind="failure",
            text=f"Fix-executor failed: {fix_failure_summary}",
            source_task_id=verifier.id,
        )
    )
    tc.graph.transition(verifier.id, Status.FAILED)
    for descendant in dependency_blocked_descendants(tc.graph, verifier_id):
        descendant.summaries.append(
            TaskSummary(
                kind="dependency_blocked",
                text=f"Blocked because dependency {verifier_id!r} failed.",
                source_task_id=verifier_id,
            )
        )
        tc._mark_terminal(descendant, Status.FAILED)
    assert verifier.task_center_harness_graph_id is not None
    close_if_terminal_verifier_failed(tc, verifier.task_center_harness_graph_id)


def handle_silent_termination(tc: "TaskCenter", task, reason: str) -> None:
    """Treat a silent verifier exit as a verification failure."""
    submit_verification_failure(tc, task.id, reason)
