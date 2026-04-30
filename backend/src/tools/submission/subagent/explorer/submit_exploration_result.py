"""submit_exploration_result terminal tool."""

from __future__ import annotations

from pydantic import BaseModel, Field

from tools.core.context import ToolExecutionContextService
from tools.core.decorator import tool
from tools.core.results import TextToolOutput, ToolResult
from tools.submission.hooks import HelperRoleGate


class SubmitExplorationResultInput(BaseModel):
    summary: str = Field(..., min_length=1)
    findings: list[str] = Field(default_factory=list)
    references: list[str] = Field(default_factory=list)


@tool(
    name="submit_exploration_result",
    description="Submit read-only explorer subagent findings.",
    input_model=SubmitExplorationResultInput,
    output_model=TextToolOutput,
    is_terminal_tool=True,
    pre_hooks=(
        HelperRoleGate(
            "submit_exploration_result",
            "explorer",
            expected_agent_type="subagent",
        ),
    ),
)
async def submit_exploration_result(
    summary: str,
    findings: list[str],
    references: list[str],
    *,
    context: ToolExecutionContextService,
) -> ToolResult:
    del context
    return ToolResult(
        output=summary,
        metadata={
            "subagent_role": "explorer",
            "findings": findings,
            "references": references,
        },
    )
