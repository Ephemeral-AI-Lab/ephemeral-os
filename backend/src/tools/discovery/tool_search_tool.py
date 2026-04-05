"""Tool for searching available tools."""

from __future__ import annotations

from pydantic import BaseModel, Field

from tools.base import BaseTool, ToolExecutionContext, ToolResult


class ToolSearchInput(BaseModel):
    """Arguments for tool search."""

    query: str = Field(description="Search query to find tools by name or description")
    max_results: int = Field(default=5, ge=1, le=50, description="Maximum number of results")


class ToolSearchTool(BaseTool):
    """Search for available tools by keyword."""

    name = "tool_search"
    description = "Search for available tools by name or description."
    input_model = ToolSearchInput

    def is_read_only(self, arguments: BaseModel) -> bool:
        return True

    async def execute(self, arguments: ToolSearchInput, context: ToolExecutionContext) -> ToolResult:
        # Stub implementation — tool search logic handled at a higher layer.
        return ToolResult(
            output=f"Search for '{arguments.query}' (max {arguments.max_results} results)",
            metadata={"query": arguments.query},
        )
