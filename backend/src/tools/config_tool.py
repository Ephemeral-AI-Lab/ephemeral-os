"""Tool for reading and updating settings."""

from __future__ import annotations

from pydantic import BaseModel, Field

from ephemeralos.config.settings import load_settings, save_settings
from ephemeralos.tools.base import BaseTool, ToolExecutionContext, ToolResult


class ConfigToolInput(BaseModel):
    """Arguments for config access."""

    action: str = Field(default="show", description="show or set")
    key: str | None = Field(default=None)
    value: str | None = Field(default=None)


class ConfigTool(BaseTool):
    """Read or update EphemeralOS settings."""

    name = "config"
    description = "Read or update EphemeralOS settings."
    input_model = ConfigToolInput

    async def execute(self, arguments: ConfigToolInput, context: ToolExecutionContext) -> ToolResult:
        del context
        settings = load_settings()
        if arguments.action == "show":
            return ToolResult(output=settings.model_dump_json(indent=2))
        if arguments.action == "set" and arguments.key and arguments.value is not None:
            if not hasattr(settings, arguments.key):
                return ToolResult(output=f"Unknown config key: {arguments.key}", is_error=True)
            setattr(settings, arguments.key, arguments.value)
            save_settings(settings)
            return ToolResult(output=f"Updated {arguments.key}")
        return ToolResult(output="Usage: action=show or action=set with key/value", is_error=True)
