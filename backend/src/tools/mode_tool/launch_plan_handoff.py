"""Terminal tool: executor or evaluator escalates by spawning a planner."""

from __future__ import annotations

from pydantic import BaseModel, Field

from tools.core.base import ToolExecutionContextService, ToolResult
from tools.core.decorator import tool
from tools.mode_tool._models import SubmissionOutput


class LaunchPlanHandoffInput(BaseModel):
    task_detail: str = Field(
        ...,
        min_length=1,
        description=(
            "What the planner needs to plan: the goal, what is uncertain, and "
            "(for evaluator-driven recovery) the specific gap to repair."
        ),
    )


@tool(
    name="launch_plan_handoff",
    description=(
        "Terminal action — escalate to a planner for the next phase. TaskCenter creates a new "
        "harness graph with this task as parent, builds a structured launch context, and spawns "
        "a planner to decompose the work. Use when the task scope expands or requires "
        "multi-step planning beyond your current role."
    ),
    input_model=LaunchPlanHandoffInput,
    output_model=SubmissionOutput,
    is_terminal_tool=True,
)
async def launch_plan_handoff(
    task_detail: str,
    *,
    context: ToolExecutionContextService,
) -> ToolResult:
    tc = context.get("task_center")
    task_id = context.get("task_id")
    if tc is None or task_id is None:
        return ToolResult(
            output="launch_plan_handoff: missing task_center or task_id in metadata",
            is_error=True,
        )
    tc.launch_plan_handoff(task_id, task_detail)
    return ToolResult(output=SubmissionOutput(status="accepted").model_dump_json())
