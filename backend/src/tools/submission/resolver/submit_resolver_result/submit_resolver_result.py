"""submit_resolver_result terminal tool."""

from __future__ import annotations

from pydantic import BaseModel, Field

from tools._framework.core.context import ToolExecutionContextService
from tools._framework.core.decorator import tool
from tools._framework.core.results import TextToolOutput, ToolResult
from .prompt import (
    get_submit_resolver_result_description,
)


class SubmitResolverResultInput(BaseModel):
    resolved: bool
    summary: str = Field(..., min_length=1)
    changed_files: list[str] = Field(default_factory=list)
    remaining_issues: list[str] = Field(default_factory=list)


@tool(
    name="submit_resolver_result",
    description=get_submit_resolver_result_description(),
    input_model=SubmitResolverResultInput,
    output_model=TextToolOutput,
    is_terminal_tool=True,
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
