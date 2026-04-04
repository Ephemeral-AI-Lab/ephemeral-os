"""Tool to list MCP resources."""

from __future__ import annotations

from pydantic import BaseModel

from ephemeralos.mcp.client import McpClientManager
from ephemeralos.tools.base import BaseTool, ToolExecutionContext, ToolResult


class ListMcpResourcesToolInput(BaseModel):
    """No-op input model for MCP resource listing."""


class ListMcpResourcesTool(BaseTool):
    """List MCP resources discovered from connected servers."""

    name = "list_mcp_resources"
    description = "List MCP resources available from connected servers."
    input_model = ListMcpResourcesToolInput

    def __init__(self, manager: McpClientManager) -> None:
        self._manager = manager

    def is_read_only(self, arguments: ListMcpResourcesToolInput) -> bool:
        del arguments
        return True

    async def execute(self, arguments: ListMcpResourcesToolInput, context: ToolExecutionContext) -> ToolResult:
        del arguments, context
        resources = self._manager.list_resources()
        if not resources:
            return ToolResult(output="(no MCP resources)")
        return ToolResult(
            output="\n".join(f"{item.server_name}:{item.uri} {item.description}".strip() for item in resources)
        )
