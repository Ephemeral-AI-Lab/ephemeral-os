"""Terminal tool: advisor emits its verdict on the calling agent's proposal."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from tools.core.base import ToolExecutionContextService, ToolResult
from tools.core.decorator import tool
from tools.mode_tool._models import SubmissionOutput


class AdvisorFeedbackInput(BaseModel):
    verdict: Literal["accept", "reject"] = Field(
        ...,
        description=(
            "'accept' means the calling agent may proceed with the proposed "
            "(terminal_tool, input) pair. 'reject' means they must call a "
            "different terminal — there is no rephrase-and-resubmit path."
        ),
    )
    reason: str = Field(
        ...,
        min_length=1,
        description=(
            "One-paragraph rationale. On reject, name the missing artifact or "
            "the policy reason; the calling agent uses this to choose its "
            "alternative terminal."
        ),
    )


@tool(
    name="submit_advisor_feedback",
    description=(
        "Terminal action (advisor only) — record your verdict on the calling "
        "agent's proposal. Verdict is 'accept' (terminal allowed) or 'reject' "
        "(terminal blocked, agent must pick a different one)."
    ),
    input_model=AdvisorFeedbackInput,
    output_model=SubmissionOutput,
    is_terminal_tool=True,
)
async def submit_advisor_feedback(
    verdict: str,
    reason: str,
    *,
    context: ToolExecutionContextService,
) -> ToolResult:
    role = context.get("role")
    if role != "advisor":
        return ToolResult(
            output=(
                "submit_advisor_feedback is advisor-only "
                f"(current role={role!r})."
            ),
            is_error=True,
        )
    tc = context.get("task_center")
    task_id = context.get("task_id")
    if tc is None or task_id is None:
        return ToolResult(
            output="submit_advisor_feedback: missing task_center or task_id in metadata",
            is_error=True,
        )
    tc.submit_advisor_feedback(task_id, verdict, reason)
    return ToolResult(output=SubmissionOutput(status="accepted").model_dump_json())
