"""Coordination planner toolkit — agent discovery and phase context queries.

Provides tools for coordinator agents to:
- Discover available specialist and coordinator agents
- Query structured outputs from completed planning phases
- Check exploration context for shared file awareness
"""

from tools.base import BaseToolkit
from tools.coordination_planner.list_agents_tool import (
    ListAvailableAgentsTool,
    ListCoordinatorAgentsTool,
    ListSpecialistAgentsTool,
)
from tools.coordination_planner.phase_context_tool import (
    ListPhasesTool,
    QueryExplorationContextTool,
    QueryPhaseContextTool,
)


class CoordinationPlannerToolkit(BaseToolkit):
    """Coordinator's introspection toolkit — agents, phases, exploration context."""

    def __init__(
        self,
        *,
        team_agent_names: list[str] | None = None,
        phase_outputs: dict[str, dict] | None = None,
    ) -> None:
        self._team_agent_names = team_agent_names
        self._phase_outputs = phase_outputs or {}
        super().__init__(
            name="coordination_planner",
            description="Agent discovery and planning phase context queries",
            tools=[
                ListSpecialistAgentsTool(team_agent_names=team_agent_names),
                ListCoordinatorAgentsTool(),
                ListAvailableAgentsTool(team_agent_names=team_agent_names),
                QueryPhaseContextTool(phase_outputs=self._phase_outputs),
                ListPhasesTool(phase_outputs=self._phase_outputs),
                QueryExplorationContextTool(),
            ],
        )


__all__ = ["CoordinationPlannerToolkit"]
