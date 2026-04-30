"""submit_resolver_result terminal tool."""

from __future__ import annotations

from pydantic import BaseModel, Field

from tools.core.context import ToolExecutionContextService
from tools.core.decorator import tool
from tools.core.results import TextToolOutput, ToolResult
from tools.submission.hooks import HelperRoleGate


class SubmitResolverResultInput(BaseModel):
    resolved: bool
    summary: str = Field(..., min_length=1)
    changed_files: list[str] = Field(default_factory=list)
    remaining_issues: list[str] = Field(default_factory=list)


@tool(
    name="submit_resolver_result",
    description="Submit resolver helper outcome.",
    input_model=SubmitResolverResultInput,
    output_model=TextToolOutput,
    is_terminal_tool=True,
    pre_hooks=(HelperRoleGate("submit_resolver_result", "resolver"),),
)
async def submit_resolver_result(
    resolved: bool,
    summary: str,
    changed_files: list[str],
    remaining_issues: list[str],
    *,
    context: ToolExecutionContextService,
) -> ToolResult:
    del context
    return ToolResult(
        output=summary,
        metadata={
            "helper_role": "resolver",
            "resolver": {
                "resolved": resolved,
                "remaining_issues": remaining_issues,
            },
            "changed_files": changed_files,
        },
    )
