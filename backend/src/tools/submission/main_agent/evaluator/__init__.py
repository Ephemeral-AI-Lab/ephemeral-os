"""Evaluator submission tools."""

from tools.submission.main_agent.evaluator.submit_evaluation_failure import (
    SubmitEvaluationFailureInput,
    submit_evaluation_failure,
)
from tools.submission.main_agent.evaluator.submit_evaluation_success import (
    SubmitEvaluationSuccessInput,
    submit_evaluation_success,
)

__all__ = [
    "SubmitEvaluationFailureInput",
    "SubmitEvaluationSuccessInput",
    "submit_evaluation_failure",
    "submit_evaluation_success",
]
