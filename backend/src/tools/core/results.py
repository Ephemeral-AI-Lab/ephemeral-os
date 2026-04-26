"""Tool execution result models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field, RootModel


@dataclass(frozen=True)
class ToolResult:
    """Normalized tool execution result."""

    output: str
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    # Set by tool execution helpers when a successful invocation of a tool with
    # ``is_terminal_tool=True`` has completed. The query loop reads this on the
    # resulting ToolResultBlock to decide whether to exit with
    # QueryExitReason.TOOL_STOP.
    does_terminate: bool = False


class TextToolOutput(RootModel[str]):
    """Successful output for tools whose true output is plain text."""

    root: str = Field(..., description="Plain text returned by the tool.")


@dataclass(frozen=True)
class ToolInputParseResult:
    """Result of validating raw tool input."""

    args: BaseModel | None = None
    error: ToolResult | None = None

    @property
    def is_error(self) -> bool:
        return self.error is not None

    @classmethod
    def success(cls, args: BaseModel) -> ToolInputParseResult:
        return cls(args=args)

    @classmethod
    def failure(cls, result: ToolResult) -> ToolInputParseResult:
        return cls(error=result)
