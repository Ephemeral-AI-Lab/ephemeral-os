"""Factory for TaskCenter submission tools."""

from __future__ import annotations

from tools._framework.core.base import BaseTool
from tools.submission.advisor import submit_advisor_feedback
from tools.submission.executor import (
    submit_generator_failure,
    submit_generator_success,
    submit_workflow_handoff,
)
from tools.submission.reducer import (
    submit_reduction_failure,
    submit_reduction_success,
)
from tools.submission.planner import (
    submit_plan_closes_goal,
    submit_plan_defers_goal,
)
from tools.submission.explorer.submit_exploration_result import submit_exploration_result


def make_submission_tools() -> list[BaseTool]:
    return [
        submit_plan_closes_goal,
        submit_plan_defers_goal,
        submit_workflow_handoff,
        submit_generator_success,
        submit_generator_failure,
        submit_reduction_success,
        submit_reduction_failure,
        submit_advisor_feedback,
        submit_exploration_result,
    ]
