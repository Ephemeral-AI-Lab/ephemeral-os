"""submit_advisor_feedback terminal tool."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from tools._framework.core.context import ToolExecutionContextService
from tools._framework.core.decorator import tool
from tools._framework.core.results import TextToolOutput, ToolResult


class SubmitAdvisorFeedbackInput(BaseModel):
    verdict: Literal["approve", "reject"]
    summary: str = Field(..., min_length=1)

    model_config = ConfigDict(extra="forbid")


@tool(
    name="submit_advisor_feedback",
    description="Submit advisor helper feedback (verdict + summary).",
    input_model=SubmitAdvisorFeedbackInput,
    output_model=TextToolOutput,
    is_terminal_tool=True,
)
async def submit_advisor_feedback(
    verdict: Literal["approve", "reject"],
    summary: str,
    *,
    context: ToolExecutionContextService,
) -> ToolResult:
    del context
    return ToolResult(
        output=summary,
        metadata={
            "helper_role": "advisor",
            "verdict": verdict,
        },
    )
