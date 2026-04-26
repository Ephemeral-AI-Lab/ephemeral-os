"""Tests for ``task_center.context.task_prompt.build_task_prompt``."""

from __future__ import annotations

from task_center import Status, Task, TaskCenterHarnessGraph, TaskSummary
from task_center.context import build_task_prompt
from task_center.task_graph import TaskGraph


def test_root_task_prompt_is_original_input() -> None:
    graph = TaskGraph()
    task = Task(id="t1", role="executor", input="User input message", status=Status.READY)
    graph.add(task)
    assert build_task_prompt(task, graph) == "User input message"


def test_planner_task_prompt_is_passed_through() -> None:
    """Planner already has the rendered PlannerLaunchContext as its input."""
    graph = TaskGraph()
    planner = Task(
        id="p", role="planner", input='{"task_detail": "go"}', status=Status.READY
    )
    graph.add(planner)
    assert build_task_prompt(planner, graph) == '{"task_detail": "go"}'


def test_executor_child_prompt_includes_completed_dependencies() -> None:
    graph = TaskGraph()
    dep = Task(id="dep", role="executor", input="dep work", status=Status.DONE)
    dep.summaries.append(TaskSummary(kind="success", text="dep done", source_task_id="dep"))
    graph.add(dep)
    child = Task(
        id="child",
        role="executor",
        input="child work",
        status=Status.READY,
        needs=frozenset({"dep"}),
    )
    graph.add(child)

    prompt = build_task_prompt(child, graph)
    assert "child work" in prompt
    assert "dep done" in prompt
    assert "completed_dependencies" in prompt


def test_evaluator_prompt_includes_parent_goal_and_child_summaries() -> None:
    graph = TaskGraph()
    parent = Task(id="p", role="executor", input="parent goal", status=Status.HANDOFF)
    graph.add(parent)
    planner = Task(
        id="pl",
        role="planner",
        input="planner ctx",
        status=Status.HANDOFF,
        task_center_harness_graph_id="g1",
    )
    planner.summaries.append(TaskSummary(kind="handoff", text="planner says", source_task_id="pl"))
    graph.add(planner)
    child = Task(
        id="c",
        role="executor",
        input="child",
        status=Status.DONE,
        task_center_harness_graph_id="g1",
    )
    child.summaries.append(TaskSummary(kind="success", text="child done", source_task_id="c"))
    graph.add(child)
    evaluator = Task(
        id="ev",
        role="evaluator",
        input="validate",
        status=Status.READY,
        task_center_harness_graph_id="g1",
    )
    graph.add(evaluator)
    graph.add_harness_graph(
        TaskCenterHarnessGraph(
            id="g1",
            run_id="r",
            parent_task_id="p",
            planner_task_id="pl",
            evaluator_task_id="ev",
            executor_task_ids=["c"],
        )
    )

    prompt = build_task_prompt(evaluator, graph)
    assert "parent goal" in prompt
    assert "planner says" in prompt
    assert "child done" in prompt


def test_evaluator_prompt_includes_nested_child_closure_summaries() -> None:
    graph = TaskGraph()
    parent = Task(id="p", role="executor", input="parent goal", status=Status.HANDOFF)
    graph.add(parent)
    planner = Task(
        id="pl",
        role="planner",
        input="planner ctx",
        status=Status.HANDOFF,
        task_center_harness_graph_id="g1",
    )
    graph.add(planner)
    nested_success = Task(
        id="nested-success",
        role="executor",
        input="delegated work",
        status=Status.DONE,
        task_center_harness_graph_id="g1",
    )
    nested_success.summaries.append(
        TaskSummary(
            kind="child_success",
            text="inner harness accepted the delegated work",
            source_task_id="inner-success-eval",
        )
    )
    graph.add(nested_success)
    nested_failure = Task(
        id="nested-failure",
        role="executor",
        input="delegated failing work",
        status=Status.FAILED,
        task_center_harness_graph_id="g1",
    )
    nested_failure.summaries.append(
        TaskSummary(
            kind="child_failure",
            text="inner harness could not satisfy the delegated work",
            source_task_id="inner-failure-eval",
        )
    )
    graph.add(nested_failure)
    evaluator = Task(
        id="ev",
        role="evaluator",
        input="validate",
        status=Status.READY,
        task_center_harness_graph_id="g1",
    )
    graph.add(evaluator)
    graph.add_harness_graph(
        TaskCenterHarnessGraph(
            id="g1",
            run_id="r",
            parent_task_id="p",
            planner_task_id="pl",
            evaluator_task_id="ev",
            executor_task_ids=["nested-success", "nested-failure"],
        )
    )

    prompt = build_task_prompt(evaluator, graph)

    assert "inner harness accepted the delegated work" in prompt
    assert "inner harness could not satisfy the delegated work" in prompt
    assert "completed_child_summaries" in prompt
    assert "failed_child_summaries" in prompt
