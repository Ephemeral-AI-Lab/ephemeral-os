"""Collaboration toolkit — multi-agent and user interaction tools."""

from ephemeralos.tools.agent_tool import AgentTool
from ephemeralos.tools.base import BaseToolkit
from ephemeralos.tools.send_message_tool import SendMessageTool
from ephemeralos.tools.team_create_tool import TeamCreateTool
from ephemeralos.tools.team_delete_tool import TeamDeleteTool


class CollaborationToolkit(BaseToolkit):
    """Multi-agent collaboration."""

    def __init__(self) -> None:
        super().__init__(
            name="collaboration",
            description="Multi-agent collaboration",
            tools=[
                AgentTool(),
                SendMessageTool(),
                TeamCreateTool(),
                TeamDeleteTool(),
            ],
        )
