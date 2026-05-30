"""submit_generator_success terminal tool."""

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

from .prompt import get_submit_generator_success_description


class SubmitGeneratorSuccessInput(BaseModel):
    outcome: str = Field(..., min_length=1)
    artifacts: list[str] = Field(default_factory=list)


@tool(
    name="submit_generator_success",
    description=get_submit_generator_success_description(),
    input_model=SubmitGeneratorSuccessInput,
    output_model=TextToolOutput,
    intent=Intent.READ_ONLY,
    is_terminal_tool=True,
    pre_hooks=(
        RequireNoInflightBackgroundTasks("submit_generator_success"),
        AdvisorApprovalPreHook("submit_generator_success"),
    ),
)
async def submit_generator_success(
    outcome: str,
    artifacts: list[str],
    *,
    context: ToolExecutionContextService,
) -> ToolResult:
    try:
        submission_context = resolve_executor_submission_context(context)
        submission_context.submit_generator_success(outcome=outcome, artifacts=artifacts)
    except (AttemptSubmissionContextError, TaskCenterInvariantViolation) as exc:
        return ToolResult(output=str(exc), is_error=True)

    return ToolResult(
        output="Accepted generator success.",
        metadata={
            "submission_kind": "generator_success",
            "task_center_task_id": submission_context.task_center_task_id,
            "attempt_id": submission_context.attempt_id,
        },
    )
