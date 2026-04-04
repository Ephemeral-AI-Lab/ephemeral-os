"""Tool for updating MCP auth configuration."""

from __future__ import annotations

from pydantic import BaseModel, Field

from ephemeralos.config.settings import load_settings, save_settings
from ephemeralos.mcp.types import McpHttpServerConfig, McpStdioServerConfig, McpWebSocketServerConfig
from ephemeralos.tools.base import BaseTool, ToolExecutionContext, ToolResult


class McpAuthToolInput(BaseModel):
    """Arguments for MCP auth updates."""

    server_name: str = Field(description="Configured MCP server name")
    mode: str = Field(description="Auth mode: bearer, header, or env")
    value: str = Field(description="Secret value to persist")
    key: str | None = Field(default=None, description="Header or env key override")


class McpAuthTool(BaseTool):
    """Persist MCP auth settings for one server."""

    name = "mcp_auth"
    description = "Configure auth for an MCP server and reconnect active sessions when possible."
    input_model = McpAuthToolInput

    async def execute(self, arguments: McpAuthToolInput, context: ToolExecutionContext) -> ToolResult:
        settings = load_settings()
        mcp_manager = context.metadata.get("mcp_manager")
        config = settings.mcp_servers.get(arguments.server_name)
        if config is None and mcp_manager is not None:
            getter = getattr(mcp_manager, "get_server_config", None)
            if callable(getter):
                config = getter(arguments.server_name)
        if config is None:
            return ToolResult(output=f"Unknown MCP server: {arguments.server_name}", is_error=True)

        if isinstance(config, McpStdioServerConfig):
            if arguments.mode not in {"env", "bearer"}:
                return ToolResult(output="stdio MCP auth supports env or bearer modes", is_error=True)
            env_key = arguments.key or "MCP_AUTH_TOKEN"
            env = dict(config.env or {})
            env[env_key] = f"Bearer {arguments.value}" if arguments.mode == "bearer" else arguments.value
            updated = config.model_copy(update={"env": env})
        elif isinstance(config, (McpHttpServerConfig, McpWebSocketServerConfig)):
            if arguments.mode not in {"header", "bearer"}:
                return ToolResult(output="http/ws MCP auth supports header or bearer modes", is_error=True)
            header_key = arguments.key or "Authorization"
            headers = dict(config.headers)
            headers[header_key] = (
                f"Bearer {arguments.value}" if arguments.mode == "bearer" and header_key == "Authorization" else arguments.value
            )
            updated = config.model_copy(update={"headers": headers})
        else:
            return ToolResult(output="Unsupported MCP server config type", is_error=True)

        settings.mcp_servers[arguments.server_name] = updated
        save_settings(settings)

        if mcp_manager is not None:
            try:
                mcp_manager.update_server_config(arguments.server_name, updated)
                await mcp_manager.reconnect_all()
            except Exception as exc:  # pragma: no cover - defensive
                return ToolResult(
                    output=f"Saved MCP auth for {arguments.server_name}, but reconnect failed: {exc}",
                    is_error=True,
                )

        return ToolResult(output=f"Saved MCP auth for {arguments.server_name}")
