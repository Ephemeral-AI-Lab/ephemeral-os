"""Terminal tool: executor declares its own task failed (soft fail)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from tools.core.base import ToolExecutionContextService, ToolResult
from tools.core.decorator import tool
from tools.mode_tool._models import SubmissionOutput


class TaskFailureInput(BaseModel):
    summary: str = Field(
        ...,
        min_length=1,
        description=(
            "Failure summary: what was attempted, what blocked it, and what "
            "evidence supports the failure."
        ),
    )


@tool(
    name="submit_task_failure",
    description=(
        "Terminal action (executor only) — mark this executor task FAILED with a summary "
        "explaining the failure. Dependency-blocked descendants are also marked FAILED; the "
        "owning graph fails if this blocks the final verifier. Use when you cannot complete "
        "the task and a planner handoff is not the right next step."
    ),
    input_model=TaskFailureInput,
    output_model=SubmissionOutput,
    is_terminal_tool=True,
)
async def submit_task_failure(
    summary: str,
    *,
    context: ToolExecutionContextService,
) -> ToolResult:
    role = context.get("role")
    if role != "executor":
        return ToolResult(
            output=(
                "submit_task_failure is executor-only "
                f"(current role={role!r})"
            ),
            is_error=True,
        )
    tc = context.get("task_center")
    task_id = context.get("task_id")
    if tc is None or task_id is None:
        return ToolResult(
            output="submit_task_failure: missing task_center or task_id in metadata",
            is_error=True,
        )
    tc.submit_task_failure(task_id, summary)
    return ToolResult(output=SubmissionOutput(status="accepted").model_dump_json())
