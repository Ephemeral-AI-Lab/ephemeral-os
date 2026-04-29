"""Planner lifecycle operations for TaskCenter."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from task_center.errors import TaskCenterError
from task_center.graph import compile_dag, plan_sinks, validate_task_ids_available
from task_center.harness_agents.planner.context import build_planner_launch_context
from task_center.model import Status, Task, TaskId, TaskSummary

if TYPE_CHECKING:
    from task_center.runtime.task_center import TaskCenter


def request_plan(tc: "TaskCenter", task_id: TaskId, request_plan_note: str) -> None:
    """Spawn a planner-owned harness graph from an executor or evaluator caller.

    The caller's input becomes the new graph's ``root_goal``; ``request_plan_note``
    is captured verbatim. Together they form the planner's prompt context.
    """
    from task_center.runtime.orchestrator import Orchestrator

    caller = tc.graph.get(task_id)
    if caller.role not in ("executor", "evaluator"):
        raise TaskCenterError(
            f"request_plan: task {task_id!r} role {caller.role!r} "
            "is not executor/evaluator"
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


def submit_plan_handoff(
    tc: "TaskCenter",
    planner_id: TaskId,
    tasks: list[dict[str, Any]],
    task_inputs: dict[str, str],
    handoff_plan_note: str,
    evaluator_note: str,
) -> None:
    """Accept a planner DAG handoff and materialize executor children + evaluator.

    ``handoff_plan_note`` describes the plan itself (PLAN_SHAPE, TOPOLOGY,
    COVERAGE_MAP, GAP). ``evaluator_note`` is the planner's explicit
    instruction to the evaluator (what to verify, what to skip, which
    adversarial probes are most relevant); it becomes the evaluator's
    task input.
    """
    planner = tc.graph.get(planner_id)
    if planner.role != "planner":
        raise TaskCenterError(
            f"submit_plan_handoff: task {planner_id!r} role {planner.role!r} "
            "is not planner"
        )
    deps = compile_dag(tasks, task_inputs)
    assert planner.task_center_harness_graph_id is not None
    graph = tc.graph.get_harness_graph(planner.task_center_harness_graph_id)
    evaluator_id = f"{planner_id}-eval"
    validate_task_ids_available(tc.graph, set(deps) | {evaluator_id})

    planner.summaries.append(
        TaskSummary(kind="handoff", text=handoff_plan_note, source_task_id=planner_id)
    )
    tc.graph.transition(planner.id, Status.HANDOFF)
    graph.handoff_plan_note = handoff_plan_note
    graph.evaluator_note = evaluator_note

    sinks = plan_sinks(deps)
    for entry in tasks:
        tid = entry["id"]
        child_status = Status.READY if not deps[tid] else Status.PENDING
        child = tc._create_executor(
            input=task_inputs[tid],
            harness_graph_id=graph.id,
            needs=deps[tid],
            status=child_status,
            id=tid,
        )
        graph.executor_task_ids.append(child.id)
        graph.dag_nodes.append(child.id)

    evaluator = tc._create_evaluator(
        input=evaluator_note,
        harness_graph_id=graph.id,
        needs=sinks,
        id=evaluator_id,
    )
    graph.evaluator_task_id = evaluator.id
    graph.evaluator = evaluator.id

    tc._persist_all()
    tc._wakeup.set()


def handle_silent_termination(tc: "TaskCenter", task: Task, reason: str) -> None:
    """Treat a silent planner exit as graph-closing planner failure."""
    from task_center.harness_agents.evaluator.lifecycle import close_harness_graph_failed

    assert task.task_center_harness_graph_id is not None
    task.summaries.append(
        TaskSummary(kind="failure", text=reason, source_task_id=task.id)
    )
    tc._mark_terminal(task, Status.FAILED)
    close_harness_graph_failed(tc, task.task_center_harness_graph_id, task.id)
    tc._persist_all()
    tc._wakeup.set()
