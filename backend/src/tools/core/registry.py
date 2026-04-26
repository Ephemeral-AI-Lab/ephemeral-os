"""Tool registry."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tools.core.base import BaseTool


class ToolRegistry:
    """Map tool names to implementations."""

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """Register a tool instance."""
        self._tools[tool.name] = tool

    def register_many(self, tools: Iterable[BaseTool]) -> None:
        """Register multiple tool instances."""
        for tool in tools:
            self.register(tool)

    def get(self, name: str) -> BaseTool | None:
        """Return a registered tool by name."""
        return self._tools.get(name)

    def list_tools(self) -> list[BaseTool]:
        """Return all registered tools."""
        return list(self._tools.values())

    def remove_tools(self, tool_names: list[str]) -> None:
        """Remove specific tools by name."""
        blocked = set(tool_names)
        self._tools = {k: v for k, v in self._tools.items() if k not in blocked}

    def restrict_to_tools(self, tool_names: list[str]) -> None:
        """Keep only the named tools."""
        allowed = set(tool_names)
        self._tools = {k: v for k, v in self._tools.items() if k in allowed}

    def to_api_schema(self) -> list[dict[str, Any]]:
        """Return all tool schemas in API format.

        Cross-cutting decorations like the optional ``background`` flag are
        applied separately by :func:`decorate_schemas_for_background` so the
        registry stays a dumb collection.
        """
        return [tool.to_api_schema() for tool in self._tools.values()]
