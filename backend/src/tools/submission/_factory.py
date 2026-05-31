"""Factory for TaskCenter submission tools."""

from __future__ import annotations

from tools._framework.core.base import BaseTool
from tools.submission.advisor import submit_advisor_feedback
from tools.submission.generator import (
    submit_generator_outcome,
    submit_workflow_handoff,
)
from tools.submission.reducer import submit_reducer_outcome
from tools.submission.planner import submit_planner_outcome
from tools.submission.explorer.submit_exploration_result import submit_exploration_result


def make_submission_tools() -> list[BaseTool]:
    return [
        submit_planner_outcome,
        submit_workflow_handoff,
        submit_generator_outcome,
        submit_reducer_outcome,
        submit_advisor_feedback,
        submit_exploration_result,
    ]
