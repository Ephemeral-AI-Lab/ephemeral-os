"""MCP configuration and state models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field


class McpStdioServerConfig(BaseModel):
    """stdio MCP server configuration."""

    type: Literal["stdio"] = "stdio"
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] | None = None
    cwd: str | None = None


class McpHttpServerConfig(BaseModel):
    """HTTP MCP server configuration."""

    type: Literal["http"] = "http"
    url: str
    headers: dict[str, str] = Field(default_factory=dict)


class McpWebSocketServerConfig(BaseModel):
    """WebSocket MCP server configuration."""

    type: Literal["ws"] = "ws"
    url: str
    headers: dict[str, str] = Field(default_factory=dict)


McpServerConfig = McpStdioServerConfig | McpHttpServerConfig | McpWebSocketServerConfig


class McpJsonConfig(BaseModel):
    """Config file shape used by plugins and project files."""

    mcpServers: dict[str, McpServerConfig] = Field(default_factory=dict)


@dataclass(frozen=True)
class McpToolInfo:
    """Tool metadata exposed by an MCP server."""

    server_name: str
    name: str
    description: str
    input_schema: dict[str, object]


@dataclass(frozen=True)
class McpResourceInfo:
    """Resource metadata exposed by an MCP server."""

    server_name: str
    name: str
    uri: str
    description: str = ""


@dataclass
class McpConnectionStatus:
    """Runtime status for one MCP server."""

    name: str
    state: Literal["connected", "failed", "pending", "disabled"]
    detail: str = ""
    transport: str = "unknown"
    auth_configured: bool = False
    tools: list[McpToolInfo] = field(default_factory=list)
    resources: list[McpResourceInfo] = field(default_factory=list)
