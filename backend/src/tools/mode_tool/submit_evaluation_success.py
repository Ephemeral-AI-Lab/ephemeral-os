"""Terminal tool: evaluator approves the harness graph (Stage 7 rename).

Mirrors the existing ``submit_task_success`` semantics for evaluators but
under the four-role-correct name. Stage 7 narrows the evaluator's
prompt + terminal surface to a single decision: was the planning unit's
goal met? The legacy ``submit_task_success`` route on TaskCenter is
preserved as a polymorphic backward-compat shim — Stage 7 ships the new
tool name + dispatcher entry; tests + agent prompts migrate over time.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from tools.core.base import ToolExecutionContextService, ToolResult
from tools.core.decorator import tool
from tools.mode_tool._models import SubmissionOutput


class EvaluationSuccessInput(BaseModel):
    summary: str = Field(
        ...,
        min_length=1,
        description="Why the parent goal was met given the children's evidence.",
    )


@tool(
    name="submit_evaluation_success",
    description=(
        "Terminal action (evaluator only) — approve the owning harness graph "
        "after independently verifying the children's evidence. The runtime "
        "decides whether to close the parent or spawn a continuation graph "
        "based on the planner's plan_shape — agents do not reason about that."
    ),
    input_model=EvaluationSuccessInput,
    output_model=SubmissionOutput,
    is_terminal_tool=True,
)
async def submit_evaluation_success(
    summary: str,
    *,
    context: ToolExecutionContextService,
) -> ToolResult:
    role = context.get("role")
    if role != "evaluator":
        return ToolResult(
            output=(
                "submit_evaluation_success is evaluator-only "
                f"(current role={role!r}); executors must use submit_task_success."
            ),
            is_error=True,
        )
    tc = context.get("task_center")
    task_id = context.get("task_id")
    if tc is None or task_id is None:
        return ToolResult(
            output="submit_evaluation_success: missing task_center or task_id in metadata",
            is_error=True,
        )
    tc.submit_evaluation_success(task_id, summary)
    return ToolResult(output=SubmissionOutput(status="accepted").model_dump_json())
