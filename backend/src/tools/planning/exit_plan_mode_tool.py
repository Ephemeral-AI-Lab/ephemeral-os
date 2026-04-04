"""Tool for exiting plan mode."""

from __future__ import annotations

from pydantic import BaseModel

from ephemeralos.tools.base import BaseTool, ToolExecutionContext, ToolResult


class ExitPlanModeInput(BaseModel):
    """Arguments for exiting plan mode."""


class ExitPlanModeTool(BaseTool):
    """Switch the agent out of planning mode back to normal execution."""

    name = "exit_plan_mode"
    description = "Exit plan mode and resume normal execution."
    input_model = ExitPlanModeInput

    async def execute(self, arguments: ExitPlanModeInput, context: ToolExecutionContext) -> ToolResult:
        del arguments
        context.metadata.pop("plan_mode", None)
        return ToolResult(output="Exited plan mode.")
