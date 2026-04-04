"""Toolkit definitions — grouped by capability."""

from ephemeralos.tools.base import (
    BaseTool,
    BaseToolkit,
    ToolExecutionContext,
    ToolRegistry,
    ToolResult,
)
from ephemeralos.tools.discovery import DiscoveryToolkit


def create_default_tool_registry() -> ToolRegistry:
    """Return the default built-in tool registry."""
    registry = ToolRegistry()
    for toolkit in (
        DiscoveryToolkit(),
    ):
        registry.register_toolkit(toolkit)
    return registry


__all__ = [
    "create_default_tool_registry",
    "BaseTool",
    "BaseToolkit",
    "ToolExecutionContext",
    "ToolRegistry",
    "ToolResult",
    "DiscoveryToolkit",
]
