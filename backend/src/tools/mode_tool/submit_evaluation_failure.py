"""Terminal tool: evaluator hard-fails the harness graph."""

from __future__ import annotations

from pydantic import BaseModel, Field

from tools.core.base import ToolExecutionContextService, ToolResult
from tools.core.decorator import tool
from tools.mode_tool._models import SubmissionOutput


class EvaluationFailureInput(BaseModel):
    summary: str = Field(
        ...,
        min_length=1,
        description=(
            "Why the parent goal cannot be met given the available evidence."
        ),
    )


@tool(
    name="submit_evaluation_failure",
    description=(
        "Terminal action (evaluator only) — hard-fail the owning harness graph after reviewing "
        "executor output. The graph's planner and parent task become FAILED, and failure "
        "propagates to outer graphs. Use when the executors' work cannot be salvaged. Prefer "
        "submit_plan_handoff when a re-plan under the same parent might recover."
    ),
    input_model=EvaluationFailureInput,
    output_model=SubmissionOutput,
    is_terminal_tool=True,
)
async def submit_evaluation_failure(
    summary: str,
    *,
    context: ToolExecutionContextService,
) -> ToolResult:
    role = context.get("role")
    if role != "evaluator":
        return ToolResult(
            output=(
                "submit_evaluation_failure is evaluator-only "
                f"(current role={role!r}); executors must use "
                "submit_task_failure instead."
            ),
            is_error=True,
        )
    tc = context.get("task_center")
    task_id = context.get("task_id")
    if tc is None or task_id is None:
        return ToolResult(
            output="submit_evaluation_failure: missing task_center or task_id in metadata",
            is_error=True,
        )
    tc.submit_evaluation_failure(task_id, summary)
    return ToolResult(output=SubmissionOutput(status="accepted").model_dump_json())
