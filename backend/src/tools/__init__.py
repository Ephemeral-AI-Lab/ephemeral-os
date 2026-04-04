"""Built-in tool registration."""

from ephemeralos.tools.base import (
    BaseTool,
    BaseToolkit,
    ToolExecutionContext,
    ToolRegistry,
    ToolResult,
)


def create_default_tool_registry(mcp_manager=None) -> ToolRegistry:
    """Return the default built-in tool registry.

    Tools are organized into toolkits but remain individually accessible
    by name for backward compatibility.

    Imports are deferred to avoid circular dependencies (toolkits import
    from ``ephemeralos.tools.base`` which lives in this package).
    """
    from ephemeralos.toolkits import (
        CodeAnalysisToolkit,
        CollaborationToolkit,
        DiscoveryToolkit,
        ExecutionToolkit,
        FilesystemToolkit,
        McpToolkit,
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
        CodeAnalysisToolkit(),
        DiscoveryToolkit(),
        SystemToolkit(),
    ):
        registry.register_toolkit(toolkit)

    # MCP toolkit needs runtime mcp_manager
    if mcp_manager is not None:
        registry.register_toolkit(McpToolkit(mcp_manager))

    return registry


__all__ = [
    "BaseTool",
    "BaseToolkit",
    "ToolExecutionContext",
    "ToolRegistry",
    "ToolResult",
    "create_default_tool_registry",
]
