"""Builders that assemble launch contexts from the live task graph."""

from __future__ import annotations

from task_center.graph.store import TaskGraph
from task_center.model import Status, Task, TaskSummary
from task_center.planning.launch_context import (
    DependencyBundle,
    EvaluatorLaunchContext,
    ExecutorLaunchContext,
    PlannerLaunchContext,
)
from task_center.summaries import child_summary_groups


def build_planner_launch_context(
    graph: TaskGraph, caller: Task, task_detail: str
) -> PlannerLaunchContext:
    """Assemble the planner input for a caller that just invoked ``launch_plan_handoff``."""
    if caller.role not in ("executor", "evaluator"):
        raise ValueError(
            "build_planner_launch_context requires an executor or evaluator caller"
        )
    upstream: list[TaskSummary] = []
    prior_handoff: list[TaskSummary] = []
    completed: list[TaskSummary] = []
    failed: list[TaskSummary] = []
    blocked: list[TaskSummary] = []
    requested_goal = caller.input

    if caller.task_center_harness_graph_id is not None:
        harness = graph.get_harness_graph(caller.task_center_harness_graph_id)
        requested_goal = graph.get(harness.parent_task_id).input
        outer_planner = graph.get(harness.planner_task_id)
        upstream = [s for s in outer_planner.summaries if s.kind == "handoff"]
        prior_handoff = list(upstream)
        for tid in harness.executor_task_ids:
            child = graph.get(tid)
            child_completed, child_failed, child_blocked = child_summary_groups(child)
            completed.extend(child_completed)
            failed.extend(child_failed)
            blocked.extend(child_blocked)

    return PlannerLaunchContext(
        task_detail=task_detail,
        caller_task_id=caller.id,
        caller_role=caller.role,
        caller_input=caller.input,
        requested_goal=requested_goal,
        upstream_handoff_summaries=upstream,
        prior_planner_handoff=prior_handoff,
        completed_child_summaries=completed,
        failed_child_summaries=failed,
        dependency_blocked_summaries=blocked,
    )


def build_executor_launch_context(
    graph: TaskGraph, task: Task
) -> ExecutorLaunchContext:
    """Bundle an executor's task input with its DONE dependency summaries."""
    if task.role != "executor":
        raise ValueError(
            "build_executor_launch_context requires an executor caller"
        )
    deps: list[DependencyBundle] = []
    for dep_id in sorted(task.needs):
        dep = graph.tasks.get(dep_id)
        if dep is None or dep.status is not Status.DONE:
            continue
        deps.append(
            DependencyBundle(
                task_id=dep.id,
                task_input=dep.input,
                summaries=list(dep.summaries),
            )
        )
    return ExecutorLaunchContext(
        task_id=task.id,
        task_input=task.input,
        harness_graph_id=task.task_center_harness_graph_id,
        completed_dependencies=deps,
    )


def build_evaluator_launch_context(
    graph: TaskGraph, task: Task
) -> EvaluatorLaunchContext | None:
    """Bundle the parent goal, planner handoff, and child summaries for an evaluator.

    Returns ``None`` when the evaluator has no associated harness graph (a
    runtime invariant violation that the caller can pass through to the agent
    as an unwrapped ``task.input``).
    """
    if task.role != "evaluator":
        raise ValueError(
            "build_evaluator_launch_context requires an evaluator caller"
        )
    if task.task_center_harness_graph_id is None:
        return None
    harness = graph.harness_graphs.get(task.task_center_harness_graph_id)
    if harness is None:
        return None
    parent = graph.tasks.get(harness.parent_task_id)
    planner = graph.tasks.get(harness.planner_task_id)
    completed: list[TaskSummary] = []
    failed: list[TaskSummary] = []
    for tid in harness.executor_task_ids:
        child = graph.tasks.get(tid)
        if child is None:
            continue
        child_completed, child_failed, child_blocked = child_summary_groups(child)
        completed.extend(child_completed)
        failed.extend([*child_failed, *child_blocked])
    return EvaluatorLaunchContext(
        task_id=task.id,
        task_input=task.input,
        harness_graph_id=task.task_center_harness_graph_id,
        parent_goal=parent.input if parent is not None else "",
        planner_handoff=[
            s
            for s in (planner.summaries if planner is not None else [])
            if s.kind == "handoff"
        ],
        completed_child_summaries=completed,
        failed_child_summaries=failed,
    )
