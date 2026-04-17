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
    """Return an empty tool registry. Toolkits are registered during agent setup."""
    return ToolRegistry()


__all__ = [
    "BaseTool",
    "BaseToolkit",
    "ToolExecutionContext",
    "ToolRegistry",
    "ToolResult",
    "create_default_tool_registry",
    "tool",
]
