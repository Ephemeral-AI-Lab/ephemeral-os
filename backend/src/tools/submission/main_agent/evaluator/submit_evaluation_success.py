"""submit_evaluation_success terminal tool."""

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
from tools.submission.hooks import HarnessRoleGate, ResolverSuccessLimitGate


class SubmitEvaluationSuccessInput(BaseModel):
    summary: str = Field(..., min_length=1)
    passed_criteria: list[str] = Field(default_factory=list)


@tool(
    name="submit_evaluation_success",
    description="Submit graph-level evaluation success.",
    input_model=SubmitEvaluationSuccessInput,
    output_model=TextToolOutput,
    is_terminal_tool=True,
    pre_hooks=(
        HarnessRoleGate("submit_evaluation_success", HarnessTaskRole.EVALUATOR),
        ResolverSuccessLimitGate("submit_evaluation_success"),
    ),
)
async def submit_evaluation_success(
    summary: str,
    passed_criteria: list[str],
    *,
    context: ToolExecutionContextService,
) -> ToolResult:
    try:
        submission_context = resolve_harness_submission_context(context)
        submission_context.orchestrator.apply_evaluator_submission(
            EvaluatorSubmission(
                graph_id=submission_context.graph.id,
                task_id=submission_context.task_center_task_id,
                outcome="success",
                summary=summary,
                payload={"passed_criteria": passed_criteria},
            )
        )
    except (HarnessSubmissionContextError, GraphInvariantViolation) as exc:
        return ToolResult(output=str(exc), is_error=True)

    return ToolResult(
        output="Accepted evaluation success.",
        metadata={
            "submission_kind": "evaluator_success",
            "task_center_task_id": submission_context.task_center_task_id,
            "harness_graph_id": submission_context.graph.id,
        },
    )
