"""Terminal tool: verifier marks node-scoped verification successful."""

from __future__ import annotations

from pydantic import BaseModel, Field

from task_center.runtime.pre_hooks import BlockedTerminal, check_advisor_accept
from tools.core.base import ToolExecutionContextService, ToolResult
from tools.core.decorator import tool
from tools.mode_tool._models import SubmissionOutput


class VerificationSuccessInput(BaseModel):
    summary: str = Field(
        ...,
        min_length=1,
        description="Why the dependencies satisfy this node's verification specification.",
    )


@tool(
    name="submit_verification_success",
    description=(
        "Terminal action (verifier only) — approve dependencies. Verifier "
        "transitions DONE; downstream DAG nodes become eligible to dispatch."
    ),
    input_model=VerificationSuccessInput,
    output_model=SubmissionOutput,
    is_terminal_tool=True,
)
async def submit_verification_success(
    summary: str,
    *,
    context: ToolExecutionContextService,
) -> ToolResult:
    role = context.get("role")
    if role != "verifier":
        return ToolResult(
            output=(
                f"submit_verification_success is verifier-only "
                f"(current role={role!r})."
            ),
            is_error=True,
        )
    tc = context.get("task_center")
    task_id = context.get("task_id")
    if tc is None or task_id is None:
        return ToolResult(
            output="submit_verification_success: missing task_center or task_id in metadata",
            is_error=True,
        )
    try:
        check_advisor_accept(
            tc, task_id, "submit_verification_success", {"summary": summary}
        )
    except BlockedTerminal as block:
        return ToolResult(output=str(block), is_error=True)
    tc.submit_verification_success(task_id, summary)
    return ToolResult(output=SubmissionOutput(status="accepted").model_dump_json())
