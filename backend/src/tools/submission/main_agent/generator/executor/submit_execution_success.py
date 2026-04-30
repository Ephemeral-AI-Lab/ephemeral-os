"""submit_execution_success terminal tool."""

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


class SubmitExecutionSuccessInput(BaseModel):
    summary: str = Field(..., min_length=1)
    artifacts: list[str] = Field(default_factory=list)


@tool(
    name="submit_execution_success",
    description="Submit successful completion of the current generator task.",
    input_model=SubmitExecutionSuccessInput,
    output_model=TextToolOutput,
    is_terminal_tool=True,
    pre_hooks=(
        HarnessRoleGate("submit_execution_success", HarnessTaskRole.GENERATOR),
    ),
)
async def submit_execution_success(
    summary: str,
    artifacts: list[str],
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
                payload={"generator_role": "executor", "artifacts": artifacts},
            )
        )
    except (HarnessSubmissionContextError, GraphInvariantViolation) as exc:
        return ToolResult(output=str(exc), is_error=True)

    return ToolResult(
        output="Accepted execution success.",
        metadata={
            "submission_kind": "generator_executor_success",
            "task_center_task_id": submission_context.task_center_task_id,
            "harness_graph_id": submission_context.graph.id,
        },
    )
