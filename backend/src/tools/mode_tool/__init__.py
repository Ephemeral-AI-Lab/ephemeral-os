"""Terminal tools for executor, planner, evaluator, and explorer agents."""

from __future__ import annotations

from tools.core.base import BaseTool
from tools.mode_tool.launch_plan_handoff import launch_plan_handoff
from tools.mode_tool.submit_evaluation_failure import submit_evaluation_failure
from tools.mode_tool.submit_exploration_result import submit_exploration_result
from tools.mode_tool.submit_plan_handoff import submit_plan_handoff
from tools.mode_tool.submit_task_failure import submit_task_failure
from tools.mode_tool.submit_task_success import submit_task_success


def make_mode_tools() -> list[BaseTool]:
    """Return terminal tools as BaseTool instances."""
    return [
        submit_task_success,
        submit_task_failure,
        submit_evaluation_failure,
        launch_plan_handoff,
        submit_plan_handoff,
        submit_exploration_result,
    ]


__all__ = [
    "launch_plan_handoff",
    "make_mode_tools",
    "submit_evaluation_failure",
    "submit_exploration_result",
    "submit_plan_handoff",
    "submit_task_failure",
    "submit_task_success",
]
