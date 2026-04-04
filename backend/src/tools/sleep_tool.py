"""Sleep tool."""

from __future__ import annotations

import asyncio

from pydantic import BaseModel, Field

from ephemeralos.tools.base import BaseTool, ToolExecutionContext, ToolResult


class SleepToolInput(BaseModel):
    """Arguments for sleep."""

    seconds: float = Field(default=1.0, ge=0.0, le=30.0)


class SleepTool(BaseTool):
    """Pause execution briefly."""

    name = "sleep"
    description = "Sleep for a short duration."
    input_model = SleepToolInput

    def is_read_only(self, arguments: SleepToolInput) -> bool:
        del arguments
        return True

    async def execute(self, arguments: SleepToolInput, context: ToolExecutionContext) -> ToolResult:
        del context
        await asyncio.sleep(arguments.seconds)
        return ToolResult(output=f"Slept for {arguments.seconds} seconds")
