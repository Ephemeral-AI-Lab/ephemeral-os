"""Tool for entering plan mode."""

from __future__ import annotations

from pydantic import BaseModel, Field

from ephemeralos.tools.base import BaseTool, ToolExecutionContext, ToolResult


class EnterPlanModeInput(BaseModel):
    """Arguments for entering plan mode."""

    reason: str = Field(default="", description="Optional reason for entering plan mode")


class EnterPlanModeTool(BaseTool):
    """Switch the agent into planning mode where it outlines steps before acting."""

    name = "enter_plan_mode"
    description = "Enter plan mode to outline steps before executing actions."
    input_model = EnterPlanModeInput

    async def execute(self, arguments: EnterPlanModeInput, context: ToolExecutionContext) -> ToolResult:
        context.metadata["plan_mode"] = True
        msg = "Entered plan mode."
        if arguments.reason:
            msg += f" Reason: {arguments.reason}"
        return ToolResult(output=msg)
