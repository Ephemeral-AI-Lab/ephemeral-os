"""Terminal tool: executor declares the task complete."""

from __future__ import annotations

from pydantic import BaseModel, Field

from tools.core.base import ToolExecutionContextService, ToolResult
from tools.core.decorator import tool
from tools.mode_tool._models import SubmissionOutput


class TaskSuccessInput(BaseModel):
    summary: str = Field(
        ...,
        min_length=1,
        description=(
            "Closure summary: a brief account of what was done and the "
            "verification evidence."
        ),
    )


@tool(
    name="submit_task_success",
    description=(
        "Terminal action (executor only) — declare the current task complete "
        "with a summary. Call exactly once when the success criteria are met. "
        "Don't call if your work is partial — use submit_task_failure or "
        "request_plan instead."
    ),
    input_model=TaskSuccessInput,
    output_model=SubmissionOutput,
    is_terminal_tool=True,
)
async def submit_task_success(
    summary: str,
    *,
    context: ToolExecutionContextService,
) -> ToolResult:
    role = context.get("role")
    if role != "executor":
        return ToolResult(
            output=(
                "submit_task_success is executor-only "
                f"(current role={role!r})"
            ),
            is_error=True,
        )
    tc = context.get("task_center")
    task_id = context.get("task_id")
    if tc is None or task_id is None:
        return ToolResult(
            output="submit_task_success: missing task_center or task_id in metadata",
            is_error=True,
        )
    tc.submit_task_success(task_id, summary)
    return ToolResult(output=SubmissionOutput(status="accepted").model_dump_json())
