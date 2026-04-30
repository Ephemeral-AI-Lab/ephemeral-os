"""Factory for TaskCenter submission tools."""

from __future__ import annotations

from tools.core.base import BaseTool
from tools.submission.helper_agent.advisor import (
    ask_advisor,
    submit_advisor_feedback,
)
from tools.submission.helper_agent.resolver import (
    ask_resolver,
    submit_resolver_result,
)
from tools.submission.main_agent.evaluator import (
    submit_evaluation_failure,
    submit_evaluation_success,
)
from tools.submission.main_agent.generator.executor import (
    request_complex_task_solution,
    submit_execution_failure,
    submit_execution_success,
)
from tools.submission.main_agent.generator.verifier import (
    submit_verification_failure,
    submit_verification_success,
)
from tools.submission.main_agent.planner import (
    submit_full_plan,
    submit_partial_plan,
)
from tools.submission.subagent.explorer import submit_exploration_result


def make_submission_tools() -> list[BaseTool]:
    return [
        submit_full_plan,
        submit_partial_plan,
        request_complex_task_solution,
        submit_execution_success,
        submit_execution_failure,
        submit_verification_success,
        submit_verification_failure,
        submit_evaluation_success,
        submit_evaluation_failure,
        ask_advisor,
        submit_advisor_feedback,
        ask_resolver,
        submit_resolver_result,
        submit_exploration_result,
    ]
