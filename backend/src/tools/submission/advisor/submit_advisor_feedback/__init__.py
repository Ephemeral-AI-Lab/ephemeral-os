"""Package for the `submit_advisor_feedback` tool."""

from . import submit_advisor_feedback as _impl

submit_advisor_feedback = _impl.submit_advisor_feedback
SubmitAdvisorFeedbackInput = _impl.SubmitAdvisorFeedbackInput

__all__ = ["SubmitAdvisorFeedbackInput", "submit_advisor_feedback"]
