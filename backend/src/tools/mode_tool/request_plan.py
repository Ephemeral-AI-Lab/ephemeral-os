"""Terminal tool: executor or evaluator requests a planner-led decomposition."""

from __future__ import annotations

from pydantic import BaseModel, Field

from tools.core.base import ToolExecutionContextService, ToolResult
from tools.core.decorator import tool
from tools.mode_tool._models import SubmissionOutput


class RequestPlanInput(BaseModel):
    request_plan_note: str = Field(
        ...,
        min_length=1,
        description=(
            "What the planner needs to plan: the goal this new harness graph "
            "must achieve as a whole, plus any constraints, partial state, or "
            "evidence the planner should consider. The text is registered "
            "verbatim on the new harness graph and rendered as the planner's "
            "## REQUEST_PLAN_NOTE."
        ),
    )


@tool(
    name="request_plan",
    description=(
        "Terminal action — request a planner-led decomposition. TaskCenter creates a new "
        "harness graph with this task as parent, captures the caller's task input as the "
        "graph's root_goal, captures request_plan_note verbatim, and spawns a planner to "
        "decompose the work. Use when the task scope expands or requires multi-step planning "
        "beyond your current role."
    ),
    input_model=RequestPlanInput,
    output_model=SubmissionOutput,
    is_terminal_tool=True,
)
async def request_plan(
    request_plan_note: str,
    *,
    context: ToolExecutionContextService,
) -> ToolResult:
    tc = context.get("task_center")
    task_id = context.get("task_id")
    if tc is None or task_id is None:
        return ToolResult(
            output="request_plan: missing task_center or task_id in metadata",
            is_error=True,
        )
    tc.request_plan(task_id, request_plan_note)
    return ToolResult(output=SubmissionOutput(status="accepted").model_dump_json())
