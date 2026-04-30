"""submit_execution_failure terminal tool."""

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
from tools.submission.hooks import HarnessRoleGate


class SubmitExecutionFailureInput(BaseModel):
    summary: str = Field(..., min_length=1)
    reason: str = Field(..., min_length=1)
    details: list[str] = Field(default_factory=list)


@tool(
    name="submit_execution_failure",
    description="Submit failed completion of the current generator task.",
    input_model=SubmitExecutionFailureInput,
    output_model=TextToolOutput,
    is_terminal_tool=True,
    pre_hooks=(
        HarnessRoleGate("submit_execution_failure", HarnessTaskRole.GENERATOR),
    ),
)
async def submit_execution_failure(
    summary: str,
    reason: str,
    details: list[str],
    *,
    context: ToolExecutionContextService,
) -> ToolResult:
    try:
        submission_context = resolve_harness_submission_context(context)
        submission_context.orchestrator.apply_generator_submission(
            GeneratorSubmission(
                graph_id=submission_context.graph.id,
                task_id=submission_context.task_center_task_id,
                outcome="failure",
                summary=summary,
                payload={
                    "generator_role": "executor",
                    "reason": reason,
                    "details": details,
                },
            )
        )
    except (HarnessSubmissionContextError, GraphInvariantViolation) as exc:
        return ToolResult(output=str(exc), is_error=True)

    return ToolResult(
        output="Accepted execution failure.",
        metadata={
            "submission_kind": "generator_executor_failure",
            "task_center_task_id": submission_context.task_center_task_id,
            "harness_graph_id": submission_context.graph.id,
        },
    )
