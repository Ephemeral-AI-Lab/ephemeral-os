"""Discovery toolkit — skill invocation and tool search."""

from tools.base import BaseToolkit
from tools.discovery.skill_tool import SkillTool
from tools.discovery.tool_search_tool import ToolSearchTool


class DiscoveryToolkit(BaseToolkit):
    """Skill invocation and tool search."""

    def __init__(self) -> None:
        super().__init__(
            name="discovery",
            description="Skill invocation and tool search",
            tools=[SkillTool(), ToolSearchTool()],
        )


__all__ = ["DiscoveryToolkit", "SkillTool", "ToolSearchTool"]
