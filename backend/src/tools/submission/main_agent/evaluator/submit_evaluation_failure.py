"""submit_evaluation_failure terminal tool."""

from __future__ import annotations

from pydantic import BaseModel, Field

from task_center.exceptions import GraphInvariantViolation
from task_center.task import EvaluatorSubmission, HarnessTaskRole
from tools.core.context import ToolExecutionContextService
from tools.core.decorator import tool
from tools.core.results import TextToolOutput, ToolResult
from tools.submission.context import (
    HarnessSubmissionContextError,
    resolve_harness_submission_context,
)
from tools.submission.hooks import HarnessRoleGate


class SubmitEvaluationFailureInput(BaseModel):
    summary: str = Field(..., min_length=1)
    failed_criteria: list[str] = Field(default_factory=list)


@tool(
    name="submit_evaluation_failure",
    description="Submit graph-level evaluation failure.",
    input_model=SubmitEvaluationFailureInput,
    output_model=TextToolOutput,
    is_terminal_tool=True,
    pre_hooks=(
        HarnessRoleGate("submit_evaluation_failure", HarnessTaskRole.EVALUATOR),
    ),
)
async def submit_evaluation_failure(
    summary: str,
    failed_criteria: list[str],
    *,
    context: ToolExecutionContextService,
) -> ToolResult:
    try:
        submission_context = resolve_harness_submission_context(context)
        submission_context.orchestrator.apply_evaluator_submission(
            EvaluatorSubmission(
                graph_id=submission_context.graph.id,
                task_id=submission_context.task_center_task_id,
                outcome="failure",
                summary=summary,
                payload={"failed_criteria": failed_criteria},
            )
        )
    except (HarnessSubmissionContextError, GraphInvariantViolation) as exc:
        return ToolResult(output=str(exc), is_error=True)

    return ToolResult(
        output="Accepted evaluation failure.",
        metadata={
            "submission_kind": "evaluator_failure",
            "task_center_task_id": submission_context.task_center_task_id,
            "harness_graph_id": submission_context.graph.id,
        },
    )
