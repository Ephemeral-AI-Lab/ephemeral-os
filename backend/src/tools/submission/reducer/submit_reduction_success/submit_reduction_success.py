"""submit_reduction_success terminal tool."""

from __future__ import annotations

from pydantic import BaseModel, Field

from task_center import (
    ReducerSubmission,
    TaskCenterInvariantViolation,
)
from tools._framework.core.context import ToolExecutionContextService
from sandbox.shared.models import Intent
from tools._framework.core.decorator import tool
from tools._framework.core.results import TextToolOutput, ToolResult
from tools._hooks.require_no_inflight_background_tasks import (
    RequireNoInflightBackgroundTasks,
)
from tools._hooks.advisor_approval import AdvisorApprovalPreHook
from tools.submission.context import (
    AttemptSubmissionContextError,
    resolve_attempt_submission_context,
)
from .prompt import (
    get_submit_reduction_success_description,
)


class SubmitReductionSuccessInput(BaseModel):
    outcome: str = Field(..., min_length=1)


@tool(
    name="submit_reduction_success",
    description=get_submit_reduction_success_description(),
    input_model=SubmitReductionSuccessInput,
    output_model=TextToolOutput,
    intent=Intent.READ_ONLY,
    is_terminal_tool=True,
    pre_hooks=(
        RequireNoInflightBackgroundTasks("submit_reduction_success"),
        AdvisorApprovalPreHook("submit_reduction_success"),
    ),
)
async def submit_reduction_success(
    outcome: str,
    *,
    context: ToolExecutionContextService,
) -> ToolResult:
    try:
        submission_context = resolve_attempt_submission_context(context)
        submission_context.orchestrator.apply_reducer_submission(
            ReducerSubmission(
                attempt_id=submission_context.attempt.id,
                task_id=submission_context.task_center_task_id,
                status="success",
                outcome=outcome,
                terminal_tool_result={},
            )
        )
    except (AttemptSubmissionContextError, TaskCenterInvariantViolation) as exc:
        return ToolResult(output=str(exc), is_error=True)

    return ToolResult(
        output="Accepted reduction success.",
        metadata={
            "submission_kind": "reduction_success",
            "task_center_task_id": submission_context.task_center_task_id,
            "attempt_id": submission_context.attempt.id,
        },
    )
