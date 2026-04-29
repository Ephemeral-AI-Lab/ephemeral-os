"""Planner lifecycle operations for TaskCenter."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from task_center.errors import TaskCenterError
from task_center.harness_agents.planner.context import build_planner_launch_context
from task_center.model import Status, Task, TaskId, TaskSummary

if TYPE_CHECKING:
    from task_center.runtime.orchestrator import MaterializationFailure
    from task_center.runtime.task_center import TaskCenter


def request_plan(tc: "TaskCenter", task_id: TaskId, request_plan_note: str) -> None:
    """Spawn a planner-owned harness graph from an executor caller.

    The caller's input becomes the new graph's ``root_goal``; ``request_plan_note``
    is captured verbatim. Together they form the planner's prompt context.
    """
    from task_center.runtime.orchestrator import Orchestrator

    caller = tc.graph.get(task_id)
    if caller.role != "executor":
        raise TaskCenterError(
            f"request_plan: task {task_id!r} role {caller.role!r} "
            "is not executor"
        )
    caller.summaries.append(
        TaskSummary(kind="handoff", text=request_plan_note, source_task_id=task_id)
    )
    tc.graph.transition(caller.id, Status.HANDOFF)

    orch = Orchestrator.spawn(
        tc,
        root_task_id=caller.id,
        request_plan_note=request_plan_note,
    )
    # The planner's input is built from a launch context (root_goal +
    # request_plan_note). ``Orchestrator.spawn`` seeded it with the raw
    # request_plan_note; rewrite it through the formal context-builder so
    # downstream prompt rendering keeps the historical structure.
    planner = orch.planner
    planner.input = build_planner_launch_context(orch.graph).to_planner_input()

    tc._persist_all()
    tc._wakeup.set()


def submit_full_plan(
    tc: "TaskCenter",
    planner_id: TaskId,
    task_dep_graphs: list[dict[str, Any]],
    task_details: dict[str, str],
) -> "MaterializationFailure | None":
    """Full-plan terminal handler.

    Runs :meth:`Orchestrator.materialize_full_plan` on the planner's graph
    and returns the validation failure (if any) so the dispatcher can
    surface it to the agent as a tool-result failure for retry.
    """
    from task_center.runtime.orchestrator import Orchestrator

    planner = tc.graph.get(planner_id)
    if planner.role != "planner":
        raise TaskCenterError(
            f"submit_full_plan: task {planner_id!r} role {planner.role!r} "
            "is not planner"
        )
    assert planner.task_center_harness_graph_id is not None
    orch = Orchestrator(
        graph_id=planner.task_center_harness_graph_id, tc=tc
    )
    return orch.materialize_full_plan(task_dep_graphs, task_details)


def submit_partial_plan(
    tc: "TaskCenter",
    planner_id: TaskId,
    task_dep_graphs: list[dict[str, Any]],
    task_details: dict[str, str],
    what_to_do_next: str,
) -> "MaterializationFailure | None":
    """Partial-plan terminal handler."""
    from task_center.runtime.orchestrator import Orchestrator

    planner = tc.graph.get(planner_id)
    if planner.role != "planner":
        raise TaskCenterError(
            f"submit_partial_plan: task {planner_id!r} role {planner.role!r} "
            "is not planner"
        )
    assert planner.task_center_harness_graph_id is not None
    orch = Orchestrator(
        graph_id=planner.task_center_harness_graph_id, tc=tc
    )
    return orch.materialize_partial_plan(
        task_dep_graphs, task_details, what_to_do_next
    )


def handle_silent_termination(tc: "TaskCenter", task: Task, reason: str) -> None:
    """Treat a silent planner exit as graph-closing planner failure."""
    from task_center.runtime.closure import close_harness_graph_failed

    assert task.task_center_harness_graph_id is not None
    task.summaries.append(
        TaskSummary(kind="failure", text=reason, source_task_id=task.id)
    )
    tc._mark_terminal(task, Status.FAILED)
    close_harness_graph_failed(tc, task.task_center_harness_graph_id, task.id)
    tc._persist_all()
    tc._wakeup.set()
