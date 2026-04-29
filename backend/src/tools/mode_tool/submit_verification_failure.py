"""Terminal tool: verifier marks node-scoped verification failed."""

from __future__ import annotations

from pydantic import BaseModel, Field

from tools.core.base import ToolExecutionContextService, ToolResult
from tools.core.decorator import tool
from tools.mode_tool._models import SubmissionOutput


class VerificationFailureInput(BaseModel):
    summary: str = Field(
        ...,
        min_length=1,
        description=(
            "Why the dependencies fail this node's verification specification. "
            "The runtime spawns a fix-executor scoped to repair the named "
            "deficiency."
        ),
    )


@tool(
    name="submit_verification_failure",
    description=(
        "Terminal action (verifier only) — reject dependencies. Verifier "
        "transitions FIXING and the runtime spawns a bounded fix-executor "
        "(Stage 6 of the four-role roadmap). On fix success, the verifier "
        "re-runs; on fix failure, dependents cascade-fail."
    ),
    input_model=VerificationFailureInput,
    output_model=SubmissionOutput,
    is_terminal_tool=True,
)
async def submit_verification_failure(
    summary: str,
    *,
    context: ToolExecutionContextService,
) -> ToolResult:
    role = context.get("role")
    if role != "verifier":
        return ToolResult(
            output=(
                f"submit_verification_failure is verifier-only "
                f"(current role={role!r})."
            ),
            is_error=True,
        )
    tc = context.get("task_center")
    task_id = context.get("task_id")
    if tc is None or task_id is None:
        return ToolResult(
            output="submit_verification_failure: missing task_center or task_id in metadata",
            is_error=True,
        )
    tc.submit_verification_failure(task_id, summary)
    return ToolResult(output=SubmissionOutput(status="accepted").model_dump_json())
