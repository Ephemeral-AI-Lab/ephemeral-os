"""Submission tools for the executor and evaluator agents.

Exports :func:`make_submission_tools` returning the BaseTool instances that
should be registered in the global tool factory.
"""

from __future__ import annotations

from tools.core.base import BaseTool
from tools.submission.enter_plan_for_handoff import enter_plan_for_handoff
from tools.submission.enter_prepare_continue_to_work import (
    enter_prepare_continue_to_work,
)
from tools.submission.submit_continue_to_work import submit_continue_to_work
from tools.submission.submit_exploration_result import submit_exploration_result
from tools.submission.submit_plan_handoff import submit_plan_handoff
from tools.submission.submit_task_completion import submit_task_completion


def make_submission_tools() -> list[BaseTool]:
    """Return the submission + mode-entry tools as BaseTool instances."""
    return [
        submit_task_completion,
        submit_plan_handoff,
        submit_continue_to_work,
        submit_exploration_result,
        enter_plan_for_handoff,
        enter_prepare_continue_to_work,
    ]


__all__ = [
    "enter_plan_for_handoff",
    "enter_prepare_continue_to_work",
    "make_submission_tools",
    "submit_continue_to_work",
    "submit_exploration_result",
    "submit_plan_handoff",
    "submit_task_completion",
]
