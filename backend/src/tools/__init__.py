"""Built-in tool registration."""

from ephemeralos.tools.base import (
    BaseTool,
    BaseToolkit,
    ToolExecutionContext,
    ToolRegistry,
    ToolResult,
)


def create_default_tool_registry() -> ToolRegistry:
    """Return the default built-in tool registry."""
    from ephemeralos.toolkits import (
        CollaborationToolkit,
        ExecutionToolkit,
        FilesystemToolkit,
        PlanningToolkit,
        SchedulingToolkit,
        SystemToolkit,
        TaskManagementToolkit,
        WebToolkit,
        WorktreeToolkit,
    )

    registry = ToolRegistry()
    for toolkit in (
        FilesystemToolkit(),
        ExecutionToolkit(),
        WebToolkit(),
        TaskManagementToolkit(),
        SchedulingToolkit(),
        WorktreeToolkit(),
        PlanningToolkit(),
        CollaborationToolkit(),
        SystemToolkit(),
    ):
        registry.register_toolkit(toolkit)

    return registry


__all__ = [
    "BaseTool",
    "BaseToolkit",
    "ToolExecutionContext",
    "ToolRegistry",
    "ToolResult",
    "create_default_tool_registry",
]
