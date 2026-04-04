"""Tool abstractions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel


@dataclass
class ToolExecutionContext:
    """Shared execution context for tool invocations."""

    cwd: Path
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolResult:
    """Normalized tool execution result."""

    output: str
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseTool(ABC):
    """Base class for all EphemeralOS tools."""

    name: str
    description: str
    input_model: type[BaseModel]

    @abstractmethod
    async def execute(self, arguments: BaseModel, context: ToolExecutionContext) -> ToolResult:
        """Execute the tool."""

    def is_read_only(self, arguments: BaseModel) -> bool:
        """Return whether the invocation is read-only."""
        del arguments
        return False

    def to_api_schema(self) -> dict[str, Any]:
        """Return the tool schema expected by the Anthropic Messages API."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_model.model_json_schema(),
        }


class BaseToolkit:
    """Named collection of related tools."""

    def __init__(
        self,
        name: str,
        description: str,
        tools: list[BaseTool] | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self._tools: dict[str, BaseTool] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: BaseTool) -> None:
        """Add a tool to this toolkit."""
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        """Return a tool by name."""
        return self._tools.get(name)

    def list_tools(self) -> list[BaseTool]:
        """Return all tools in this toolkit."""
        return list(self._tools.values())

    def tool_names(self) -> list[str]:
        """Return names of all tools in this toolkit."""
        return list(self._tools.keys())


class ToolRegistry:
    """Map tool names to implementations, with optional toolkit grouping."""

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}
        self._toolkits: dict[str, BaseToolkit] = {}

    def register(self, tool: BaseTool) -> None:
        """Register a tool instance."""
        self._tools[tool.name] = tool

    def register_toolkit(self, toolkit: BaseToolkit) -> None:
        """Register a toolkit and all its tools individually."""
        self._toolkits[toolkit.name] = toolkit
        for tool in toolkit.list_tools():
            self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        """Return a registered tool by name."""
        return self._tools.get(name)

    def get_toolkit(self, name: str) -> BaseToolkit | None:
        """Return a registered toolkit by name."""
        return self._toolkits.get(name)

    def list_tools(self) -> list[BaseTool]:
        """Return all registered tools."""
        return list(self._tools.values())

    def list_toolkits(self) -> list[BaseToolkit]:
        """Return all registered toolkits."""
        return list(self._toolkits.values())

    def restrict_to_toolkits(self, toolkit_names: list[str]) -> None:
        """Remove all tools and toolkits not in *toolkit_names*."""
        allowed = set(toolkit_names)
        allowed_tools: set[str] = set()
        kept_toolkits: dict[str, BaseToolkit] = {}
        for name, tk in self._toolkits.items():
            if name in allowed:
                kept_toolkits[name] = tk
                allowed_tools.update(tk.tool_names())
        self._toolkits = kept_toolkits
        self._tools = {k: v for k, v in self._tools.items() if k in allowed_tools}

    def to_api_schema(self) -> list[dict[str, Any]]:
        """Return all tool schemas in API format."""
        return [tool.to_api_schema() for tool in self._tools.values()]
