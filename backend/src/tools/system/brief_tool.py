"""Tool for producing a brief summary."""

from __future__ import annotations

from pydantic import BaseModel, Field

from ephemeralos.tools.base import BaseTool, ToolExecutionContext, ToolResult


class BriefToolInput(BaseModel):
    """Arguments for brief mode transformation."""

    text: str = Field(description="Text to shorten")
    max_chars: int = Field(default=200, ge=20, le=2000)


class BriefTool(BaseTool):
    """Return a shortened version of text."""

    name = "brief"
    description = "Shorten a piece of text for compact display."
    input_model = BriefToolInput

    def is_read_only(self, arguments: BriefToolInput) -> bool:
        del arguments
        return True

    async def execute(self, arguments: BriefToolInput, context: ToolExecutionContext) -> ToolResult:
        del context
        text = arguments.text.strip()
        if len(text) <= arguments.max_chars:
            return ToolResult(output=text)
        return ToolResult(output=text[: arguments.max_chars].rstrip() + "...")
