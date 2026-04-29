"""Terminal tools for executor, planner, evaluator, and explorer agents."""

from __future__ import annotations

from tools.core.base import BaseTool
from tools.ask_advisor import ask_advisor
from tools.mode_tool.request_plan import request_plan
from tools.mode_tool.submit_advisor_feedback import submit_advisor_feedback
from tools.mode_tool.submit_evaluation_failure import submit_evaluation_failure
from tools.mode_tool.submit_evaluation_success import submit_evaluation_success
from tools.mode_tool.submit_exploration_result import submit_exploration_result
from tools.mode_tool.submit_full_plan import submit_full_plan
from tools.mode_tool.submit_partial_plan import submit_partial_plan
from tools.mode_tool.submit_task_failure import submit_task_failure
from tools.mode_tool.submit_task_success import submit_task_success
from tools.mode_tool.submit_verification_failure import submit_verification_failure
from tools.mode_tool.submit_verification_success import submit_verification_success


def make_mode_tools() -> list[BaseTool]:
    """Return terminal tools as BaseTool instances."""
    return [
        submit_task_success,
        submit_task_failure,
        submit_evaluation_success,
        submit_evaluation_failure,
        submit_advisor_feedback,
        submit_verification_success,
        submit_verification_failure,
        request_plan,
        submit_full_plan,
        submit_partial_plan,
        submit_exploration_result,
        ask_advisor,
    ]


__all__ = [
    "ask_advisor",
    "request_plan",
    "make_mode_tools",
    "submit_advisor_feedback",
    "submit_evaluation_failure",
    "submit_evaluation_success",
    "submit_exploration_result",
    "submit_full_plan",
    "submit_partial_plan",
    "submit_task_failure",
    "submit_task_success",
    "submit_verification_failure",
    "submit_verification_success",
]
