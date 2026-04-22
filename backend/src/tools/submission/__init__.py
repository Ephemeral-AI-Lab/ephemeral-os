"""Submission tools — terminal actions for team-mode agents."""

from tools.submission.toolkit import (
    SubmissionToolkit,
    SubmitPlanTool,
    SubmitReplanTool,
    SubmitTaskSummaryTool,
)

# Side-effect import: registers submission platform hooks on the default hook
# registry whenever the toolkit package loads.
from tools.submission import hooks as _hooks  # noqa: F401

__all__ = [
    "SubmitPlanTool",
    "SubmitReplanTool",
    "SubmitTaskSummaryTool",
    "SubmissionToolkit",
]
