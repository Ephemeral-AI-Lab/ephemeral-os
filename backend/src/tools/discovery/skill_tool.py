"""Tool for invoking skills."""

from __future__ import annotations

from pydantic import BaseModel, Field

from tools.base import BaseTool, ToolExecutionContext, ToolResult


class SkillToolInput(BaseModel):
    """Arguments for the skill tool."""

    skill: str = Field(description="The skill name to invoke")
    args: str = Field(default="", description="Optional arguments for the skill")


class SkillTool(BaseTool):
    """Invoke a registered skill by name."""

    name = "skill"
    description = "Invoke a registered skill by name."
    input_model = SkillToolInput

    async def execute(self, arguments: SkillToolInput, context: ToolExecutionContext) -> ToolResult:
        # Stub implementation — skill dispatch handled at a higher layer.
        return ToolResult(
            output=f"Skill '{arguments.skill}' invoked with args: {arguments.args or '(none)'}",
            metadata={"skill": arguments.skill},
        )
