"""Core tool abstraction.

This module remains the compatibility import surface for core tool types. The
supporting context, registry, result, and validation helpers live in focused
modules to keep the base abstraction small.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel

from tools.core.context import ToolExecutionContextService
from tools.core.results import TextToolOutput, ToolResult
from tools.core.runtime import ExecutionMetadata

if TYPE_CHECKING:
    from tools.core.registry import ToolRegistry

__all__ = [
    "BackgroundMode",
    "BaseTool",
    "ExecutionMetadata",
    "TextToolOutput",
    "ToolExecutionContextService",
    "ToolRegistry",
    "ToolResult",
]


BackgroundMode = Literal["forbidden", "optional", "always"]


def __getattr__(name: str) -> Any:
    if name == "ToolRegistry":
        from tools.core.registry import ToolRegistry

        return ToolRegistry
    raise AttributeError(name)


class BaseTool(ABC):
    """Base class for all EphemeralOS tools."""

    name: str
    description: str
    short_description: str | None = None
    input_model: type[BaseModel]
    output_model: type[BaseModel] = TextToolOutput
    # Background dispatch policy:
    #   "forbidden" — tool cannot run in background (default)
    #   "optional"  — LLM may opt in by passing background=true
    #   "always"    — engine ALWAYS dispatches as background, regardless of input
    background: BackgroundMode = "forbidden"
    # Discriminator for monitoring/UI/audit so the engine never sniffs tool names.
    # "agent" is the default for ordinary background tools; tools that spawn a
    # nested agent (e.g. run_subagent) override it to "subagent".
    task_type: str = "agent"
    # When True, a successful invocation ends the agent run. The
    # engine stamps does_terminate=True on the resulting ToolResult and the
    # query loop exits with TOOL_STOP after the response completes.
    is_terminal_tool: bool = False
    # Tool-specific hooks. These are intentionally explicit per tool and do
    # not affect the LLM-facing schema.
    pre_hooks: tuple[Any, ...] = ()
    post_hooks: tuple[Any, ...] = ()
    # Runtime context dependencies declared by tools. Runtime assembly uses
    # these markers to attach provider-specific context preparers without the
    # core query loop sniffing tool names.
    context_requirements: tuple[str, ...] = ()

    @abstractmethod
    async def execute(
        self,
        arguments: BaseModel,
        context: ToolExecutionContextService,
    ) -> ToolResult:
        """Execute the tool."""

    def output_schema(self) -> dict[str, Any]:
        """Return the output JSON Schema for successful tool output."""
        return self.output_model.model_json_schema()

    def to_api_schema(self) -> dict[str, Any]:
        """Return the tool schema expected by the Anthropic Messages API."""
        schema: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_model.model_json_schema(),
        }
        schema["output_schema"] = self.output_schema()
        return schema
