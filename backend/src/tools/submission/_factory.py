"""Factory for TaskCenter submission tools."""

from __future__ import annotations

from tools._framework.core.base import BaseTool
from tools.submission.advisor import submit_advisor_feedback
from tools.submission.resolver import submit_resolver_result
from tools.submission.evaluator import (
    submit_evaluation_failure,
    submit_evaluation_success,
)
from tools.submission.executor import request_mission_solution
from tools.submission.executor import (
    submit_execution_failure,
    submit_execution_success,
)
from tools.submission.verifier import (
    submit_verification_failure,
    submit_verification_success,
)
from tools.submission.planner import (
    submit_full_plan,
    submit_partial_plan,
)
from tools.submission.explorer.submit_exploration_result import submit_exploration_result


def make_submission_tools() -> list[BaseTool]:
    return [
        submit_full_plan,
        submit_partial_plan,
        request_mission_solution,
        submit_execution_success,
        submit_execution_failure,
        submit_verification_success,
        submit_verification_failure,
        submit_evaluation_success,
        submit_evaluation_failure,
        submit_advisor_feedback,
        submit_resolver_result,
        submit_exploration_result,
    ]
