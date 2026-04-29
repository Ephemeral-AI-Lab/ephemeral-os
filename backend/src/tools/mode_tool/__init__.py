"""Terminal tools for executor, planner, evaluator, and explorer agents."""

from __future__ import annotations

from tools.core.base import BaseTool
from tools.mode_tool.request_plan import request_plan
from tools.mode_tool.submit_evaluation_failure import submit_evaluation_failure
from tools.mode_tool.submit_evaluation_success import submit_evaluation_success
from tools.mode_tool.submit_exploration_result import submit_exploration_result
from tools.mode_tool.submit_full_plan import submit_full_plan
from tools.mode_tool.submit_partial_plan import submit_partial_plan
from tools.mode_tool.submit_plan_handoff import submit_plan_handoff
from tools.mode_tool.submit_task_failure import submit_task_failure
from tools.mode_tool.submit_task_success import submit_task_success


def make_mode_tools() -> list[BaseTool]:
    """Return terminal tools as BaseTool instances."""
    return [
        submit_task_success,
        submit_task_failure,
        submit_evaluation_success,
        submit_evaluation_failure,
        request_plan,
        submit_plan_handoff,
        submit_full_plan,
        submit_partial_plan,
        submit_exploration_result,
    ]


__all__ = [
    "request_plan",
    "make_mode_tools",
    "submit_evaluation_failure",
    "submit_evaluation_success",
    "submit_exploration_result",
    "submit_full_plan",
    "submit_partial_plan",
    "submit_plan_handoff",
    "submit_task_failure",
    "submit_task_success",
]
