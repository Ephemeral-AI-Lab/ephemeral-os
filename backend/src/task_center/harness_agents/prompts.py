"""Build role-specific task prompts for harness agents at dispatch time."""

from __future__ import annotations

from task_center.graph import TaskGraph
from task_center.harness_agents.evaluator.context import build_evaluator_launch_context
from task_center.harness_agents.executor.context import build_executor_launch_context
from task_center.model import Task


def build_task_prompt(task: Task, graph: TaskGraph) -> str:
    """Return the user/task prompt with role-specific context wrapped in."""
    if task.role == "planner":
        return task.input
    if task.role == "executor":
        return build_executor_launch_context(graph, task).to_executor_prompt()
    if task.role == "evaluator":
        ctx = build_evaluator_launch_context(graph, task)
        if ctx is None:
            return task.input
        return ctx.to_evaluator_prompt()
    return task.input
