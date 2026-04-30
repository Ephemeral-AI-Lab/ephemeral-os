"""submit_verification_success terminal tool."""

from __future__ import annotations

from pydantic import BaseModel, Field

from task_center.exceptions import GraphInvariantViolation
from task_center.task import GeneratorSubmission, HarnessTaskRole
from tools.core.context import ToolExecutionContextService
from tools.core.decorator import tool
from tools.core.results import TextToolOutput, ToolResult
from tools.submission.context import (
    HarnessSubmissionContextError,
    resolve_harness_submission_context,
)
from tools.submission.hooks import HarnessRoleGate, ResolverSuccessLimitGate


class SubmitVerificationSuccessInput(BaseModel):
    summary: str = Field(..., min_length=1)
    checks: list[str] = Field(default_factory=list)


@tool(
    name="submit_verification_success",
    description="Submit successful verification of the current generator task.",
    input_model=SubmitVerificationSuccessInput,
    output_model=TextToolOutput,
    is_terminal_tool=True,
    pre_hooks=(
        HarnessRoleGate("submit_verification_success", HarnessTaskRole.GENERATOR),
        ResolverSuccessLimitGate("submit_verification_success"),
    ),
)
async def submit_verification_success(
    summary: str,
    checks: list[str],
    *,
    context: ToolExecutionContextService,
) -> ToolResult:
    try:
        submission_context = resolve_harness_submission_context(context)
        submission_context.orchestrator.apply_generator_submission(
            GeneratorSubmission(
                graph_id=submission_context.graph.id,
                task_id=submission_context.task_center_task_id,
                outcome="success",
                summary=summary,
                payload={"generator_role": "verifier", "checks": checks},
            )
        )
    except (HarnessSubmissionContextError, GraphInvariantViolation) as exc:
        return ToolResult(output=str(exc), is_error=True)

    return ToolResult(
        output="Accepted verification success.",
        metadata={
            "submission_kind": "generator_verifier_success",
            "task_center_task_id": submission_context.task_center_task_id,
            "harness_graph_id": submission_context.graph.id,
        },
    )
