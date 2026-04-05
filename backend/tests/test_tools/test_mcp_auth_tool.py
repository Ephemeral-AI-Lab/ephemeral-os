"""Tests for MCP auth tool persistence and reconnect behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from config.settings import Settings, load_settings, save_settings
from mcp.types import McpHttpServerConfig, McpStdioServerConfig
from tools.base import ToolExecutionContext
from tools.mcp_auth_tool import McpAuthTool, McpAuthToolInput


class FakeMcpManager:
    """Tiny fake MCP manager for reconnect assertions."""

    def __init__(self) -> None:
        self.updated: list[tuple[str, object]] = []
        self.reconnected = 0

    def update_server_config(self, name: str, config: object) -> None:
        self.updated.append((name, config))

    def get_server_config(self, name: str) -> object | None:
        for server_name, config in self.updated:
            if server_name == name:
                return config
        return self._seed.get(name) if hasattr(self, "_seed") else None

    async def reconnect_all(self) -> None:
        self.reconnected += 1


@pytest.mark.asyncio
async def test_mcp_auth_tool_updates_http_headers(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("EPHEMERALOS_CONFIG_DIR", str(tmp_path / "config"))
    save_settings(
        Settings(
            mcp_servers={
                "demo": McpHttpServerConfig(url="https://example.com/mcp"),
            }
        )
    )
    manager = FakeMcpManager()
    context = ToolExecutionContext(cwd=tmp_path, metadata={"mcp_manager": manager})

    result = await McpAuthTool().execute(
        McpAuthToolInput(server_name="demo", mode="bearer", value="secret"),
        context,
    )

    assert result.is_error is False
    assert "Saved MCP auth for demo" in result.output
    saved = load_settings().mcp_servers["demo"]
    assert saved.headers["Authorization"] == "Bearer secret"
    assert manager.updated[0][0] == "demo"
    assert manager.reconnected == 1


@pytest.mark.asyncio
async def test_mcp_auth_tool_updates_stdio_env(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("EPHEMERALOS_CONFIG_DIR", str(tmp_path / "config"))
    save_settings(
        Settings(
            mcp_servers={
                "fixture": McpStdioServerConfig(command="python", args=["-m", "fixture"]),
            }
        )
    )
    context = ToolExecutionContext(cwd=tmp_path)

    result = await McpAuthTool().execute(
        McpAuthToolInput(server_name="fixture", mode="env", key="FIXTURE_TOKEN", value="abc123"),
        context,
    )

    assert result.is_error is False
    saved = load_settings().mcp_servers["fixture"]
    assert saved.env["FIXTURE_TOKEN"] == "abc123"


@pytest.mark.asyncio
async def test_mcp_auth_tool_can_start_from_active_manager_config(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("EPHEMERALOS_CONFIG_DIR", str(tmp_path / "config"))
    save_settings(Settings())
    manager = FakeMcpManager()
    manager._seed = {
        "fixture": McpStdioServerConfig(command="python", args=["-m", "fixture"]),
    }
    context = ToolExecutionContext(cwd=tmp_path, metadata={"mcp_manager": manager})

    result = await McpAuthTool().execute(
        McpAuthToolInput(server_name="fixture", mode="bearer", value="token-smoke"),
        context,
    )

    assert result.is_error is False
    saved = load_settings().mcp_servers["fixture"]
    assert saved.env["MCP_AUTH_TOKEN"] == "Bearer token-smoke"
    assert manager.reconnected == 1
