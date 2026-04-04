"""MCP client manager."""

from __future__ import annotations

from contextlib import AsyncExitStack
from typing import Any

from ephemeralos.mcp.types import (
    McpConnectionStatus,
    McpResourceInfo,
    McpStdioServerConfig,
    McpToolInfo,
)
from ephemeralos.mcp._compat import load_external_mcp

_external_mcp = load_external_mcp()
ClientSession = _external_mcp.ClientSession
StdioServerParameters = _external_mcp.StdioServerParameters
stdio_client = _external_mcp.client.stdio.stdio_client
CallToolResult = _external_mcp.types.CallToolResult
ReadResourceResult = _external_mcp.types.ReadResourceResult

class McpClientManager:
    """Manage MCP connections and expose tools/resources."""

    def __init__(self, server_configs: dict[str, object]) -> None:
        self._server_configs = server_configs
        self._statuses: dict[str, McpConnectionStatus] = {
            name: McpConnectionStatus(
                name=name,
                state="pending",
                transport=getattr(config, "type", "unknown"),
            )
            for name, config in server_configs.items()
        }
        self._sessions: dict[str, ClientSession] = {}
        self._stacks: dict[str, AsyncExitStack] = {}

    async def connect_all(self) -> None:
        """Connect all configured stdio MCP servers."""
        for name, config in self._server_configs.items():
            if isinstance(config, McpStdioServerConfig):
                await self._connect_stdio(name, config)
            else:
                self._statuses[name] = McpConnectionStatus(
                    name=name,
                    state="failed",
                    transport=config.type,
                    auth_configured=bool(getattr(config, "headers", None)),
                    detail=f"Unsupported MCP transport in current build: {config.type}",
                )

    async def reconnect_all(self) -> None:
        """Reconnect all configured servers."""
        await self.close()
        self._statuses = {
            name: McpConnectionStatus(name=name, state="pending", transport=getattr(config, "type", "unknown"))
            for name, config in self._server_configs.items()
        }
        await self.connect_all()

    def update_server_config(self, name: str, config: object) -> None:
        """Replace one server config in memory."""
        self._server_configs[name] = config

    def get_server_config(self, name: str) -> object | None:
        """Return one configured server object if present."""
        return self._server_configs.get(name)

    async def close(self) -> None:
        """Close all active MCP sessions."""
        for stack in list(self._stacks.values()):
            await stack.aclose()
        self._stacks.clear()
        self._sessions.clear()

    def list_statuses(self) -> list[McpConnectionStatus]:
        """Return statuses for all configured servers."""
        return [self._statuses[name] for name in sorted(self._statuses)]

    def list_tools(self) -> list[McpToolInfo]:
        """Return all connected MCP tools."""
        tools: list[McpToolInfo] = []
        for status in self.list_statuses():
            tools.extend(status.tools)
        return tools

    def list_resources(self) -> list[McpResourceInfo]:
        """Return all connected MCP resources."""
        resources: list[McpResourceInfo] = []
        for status in self.list_statuses():
            resources.extend(status.resources)
        return resources

    async def call_tool(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> str:
        """Invoke one MCP tool and stringify the result."""
        session = self._sessions[server_name]
        result: CallToolResult = await session.call_tool(tool_name, arguments)
        parts: list[str] = []
        for item in result.content:
            if getattr(item, "type", None) == "text":
                parts.append(getattr(item, "text", ""))
            else:
                parts.append(item.model_dump_json())
        if result.structuredContent and not parts:
            parts.append(str(result.structuredContent))
        if not parts:
            parts.append("(no output)")
        return "\n".join(parts).strip()

    async def read_resource(self, server_name: str, uri: str) -> str:
        """Read one MCP resource and stringify the response."""
        session = self._sessions[server_name]
        result: ReadResourceResult = await session.read_resource(uri)
        parts: list[str] = []
        for item in result.contents:
            text = getattr(item, "text", None)
            if text is not None:
                parts.append(text)
            else:
                parts.append(str(getattr(item, "blob", "")))
        return "\n".join(parts).strip()

    async def _connect_stdio(self, name: str, config: McpStdioServerConfig) -> None:
        stack = AsyncExitStack()
        try:
            read_stream, write_stream = await stack.enter_async_context(
                stdio_client(
                    StdioServerParameters(
                        command=config.command,
                        args=config.args,
                        env=config.env,
                        cwd=config.cwd,
                    )
                )
            )
            session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
            await session.initialize()
            tool_result = await session.list_tools()
            resource_result = await session.list_resources()
            tools = [
                McpToolInfo(
                    server_name=name,
                    name=tool.name,
                    description=tool.description or "",
                    input_schema=dict(tool.inputSchema or {"type": "object", "properties": {}}),
                )
                for tool in tool_result.tools
            ]
            resources = [
                McpResourceInfo(
                    server_name=name,
                    name=resource.name or str(resource.uri),
                    uri=str(resource.uri),
                    description=resource.description or "",
                )
                for resource in resource_result.resources
            ]
            self._sessions[name] = session
            self._stacks[name] = stack
            self._statuses[name] = McpConnectionStatus(
                name=name,
                state="connected",
                transport=config.type,
                auth_configured=bool(config.env),
                tools=tools,
                resources=resources,
            )
        except Exception as exc:
            await stack.aclose()
            self._statuses[name] = McpConnectionStatus(
                name=name,
                state="failed",
                transport=config.type,
                auth_configured=bool(config.env),
                detail=str(exc),
            )
