"""Tool for entering plan permission mode."""

from __future__ import annotations

from pydantic import BaseModel

from ephemeralos.config.settings import load_settings, save_settings
from ephemeralos.permissions import PermissionMode
from ephemeralos.tools.base import BaseTool, ToolExecutionContext, ToolResult


class EnterPlanModeToolInput(BaseModel):
    """No-op input model."""


class EnterPlanModeTool(BaseTool):
    """Switch settings permission mode to plan."""

    name = "enter_plan_mode"
    description = "Switch permission mode to plan."
    input_model = EnterPlanModeToolInput

    async def execute(self, arguments: EnterPlanModeToolInput, context: ToolExecutionContext) -> ToolResult:
        del arguments, context
        settings = load_settings()
        settings.permission.mode = PermissionMode.PLAN
        save_settings(settings)
        return ToolResult(output="Permission mode set to plan")
