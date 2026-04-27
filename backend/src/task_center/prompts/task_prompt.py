"""Build the prompt sent to an agent for one task at dispatch time.

The launch-context dataclasses in ``task_center.planning`` own the wire format
for each role; this module is a thin dispatcher.
"""

from __future__ import annotations

from task_center.graph import TaskGraph
from task_center.model import Task
from task_center.planning import (
    build_evaluator_launch_context,
    build_executor_launch_context,
)


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
