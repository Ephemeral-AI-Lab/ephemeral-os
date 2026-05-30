"""submit_generator_failure terminal tool."""

from __future__ import annotations

from pydantic import BaseModel, Field

from sandbox.shared.models import Intent
from task_center import TaskCenterInvariantViolation
from tools._framework.core.context import ToolExecutionContextService
from tools._framework.core.decorator import tool
from tools._framework.core.results import TextToolOutput, ToolResult
from tools._hooks.advisor_approval import AdvisorApprovalPreHook
from tools._hooks.require_no_inflight_background_tasks import (
    RequireNoInflightBackgroundTasks,
)
from tools.submission.context import (
    AttemptSubmissionContextError,
    resolve_executor_submission_context,
)

from .prompt import get_submit_generator_failure_description


class SubmitGeneratorFailureInput(BaseModel):
    outcome: str = Field(..., min_length=1)


@tool(
    name="submit_generator_failure",
    description=get_submit_generator_failure_description(),
    input_model=SubmitGeneratorFailureInput,
    output_model=TextToolOutput,
    intent=Intent.READ_ONLY,
    is_terminal_tool=True,
    pre_hooks=(
        RequireNoInflightBackgroundTasks("submit_generator_failure"),
        AdvisorApprovalPreHook("submit_generator_failure"),
    ),
)
async def submit_generator_failure(
    outcome: str,
    *,
    context: ToolExecutionContextService,
) -> ToolResult:
    try:
        submission_context = resolve_executor_submission_context(context)
        submission_context.submit_generator_failure(outcome=outcome)
    except (AttemptSubmissionContextError, TaskCenterInvariantViolation) as exc:
        return ToolResult(output=str(exc), is_error=True)

    return ToolResult(
        output="Accepted generator failure.",
        metadata={
            "submission_kind": "generator_failure",
            "task_center_task_id": submission_context.task_center_task_id,
            "attempt_id": submission_context.attempt_id,
        },
    )
