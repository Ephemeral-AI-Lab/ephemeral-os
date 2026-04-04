"""Tool for searching available tools."""

from __future__ import annotations

from pydantic import BaseModel, Field

from ephemeralos.tools.base import BaseTool, ToolExecutionContext, ToolResult


class ToolSearchToolInput(BaseModel):
    """Arguments for tool search."""

    query: str = Field(description="Substring to search in tool names and descriptions")


class ToolSearchTool(BaseTool):
    """Search tool registry contents."""

    name = "tool_search"
    description = "Search the available tool list by name or description."
    input_model = ToolSearchToolInput

    def is_read_only(self, arguments: ToolSearchToolInput) -> bool:
        del arguments
        return True

    async def execute(self, arguments: ToolSearchToolInput, context: ToolExecutionContext) -> ToolResult:
        registry = context.metadata.get("tool_registry") if hasattr(context, "metadata") else None
        if registry is None:
            return ToolResult(output="Tool registry context not available", is_error=True)
        query = arguments.query.lower()
        matches = [
            tool for tool in registry.list_tools()
            if query in tool.name.lower() or query in tool.description.lower()
        ]
        if not matches:
            return ToolResult(output="(no matches)")
        return ToolResult(output="\n".join(f"{tool.name}: {tool.description}" for tool in matches))
