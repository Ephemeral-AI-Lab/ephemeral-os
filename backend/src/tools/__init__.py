"""Toolkit definitions — grouped by capability."""

from tools.core import (
    BaseTool,
    BaseToolkit,
    ToolExecutionContext,
    ToolRegistry,
    ToolResult,
    tool,
)


def create_default_tool_registry() -> ToolRegistry:
    """Return an empty tool registry. Toolkits are added via the factory."""
    return ToolRegistry()


__all__ = [
    "create_default_tool_registry",
    "BaseTool",
    "BaseToolkit",
    "ToolExecutionContext",
    "ToolRegistry",
    "ToolResult",
    "tool",
]
