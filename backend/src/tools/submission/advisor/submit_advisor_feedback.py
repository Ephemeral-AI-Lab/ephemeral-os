"""submit_advisor_feedback terminal tool."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from tools._framework.core.context import ToolExecutionContextService
from tools._framework.core.decorator import tool
from tools._framework.core.results import TextToolOutput, ToolResult


class SubmitAdvisorFeedbackInput(BaseModel):
    verdict: Literal["approve", "revise", "reject"]
    summary: str = Field(..., min_length=1)
    risks: list[str] = Field(default_factory=list)


@tool(
    name="submit_advisor_feedback",
    description="Submit advisor helper feedback.",
    input_model=SubmitAdvisorFeedbackInput,
    output_model=TextToolOutput,
    is_terminal_tool=True,
)
async def submit_advisor_feedback(
    verdict: Literal["approve", "revise", "reject"],
    summary: str,
    risks: list[str],
    *,
    context: ToolExecutionContextService,
) -> ToolResult:
    del context
    return ToolResult(
        output=summary,
        metadata={
            "helper_role": "advisor",
            "verdict": verdict,
            "risks": risks,
        },
    )
