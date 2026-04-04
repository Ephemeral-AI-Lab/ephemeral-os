"""Load MCP server config from settings."""

from __future__ import annotations


def load_mcp_server_configs(settings) -> dict[str, object]:
    """Return MCP server configs from settings."""
    return dict(settings.mcp_servers)
