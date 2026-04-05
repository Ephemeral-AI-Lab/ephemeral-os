"""Coordination planner toolkit — agent discovery and phase context queries."""

from tools.base import BaseToolkit
from tools.coordination_planner.list_agents_tool import ListAgentsTool
from tools.coordination_planner.phase_context_tool import (
    ListPhasesTool,
    QueryPhaseContextTool,
)


class CoordinationPlannerToolkit(BaseToolkit):
    """Coordinator's introspection toolkit — agents and phase context."""

    def __init__(
        self,
        *,
        agent_names: list[str] | None = None,
        phase_outputs: dict[str, dict] | None = None,
    ) -> None:
        super().__init__(
            name="coordination_planner",
            description="Agent discovery and planning phase context queries",
            tools=[
                ListAgentsTool(agent_names=agent_names),
                QueryPhaseContextTool(phase_outputs=phase_outputs or {}),
                ListPhasesTool(phase_outputs=phase_outputs or {}),
            ],
        )


__all__ = ["CoordinationPlannerToolkit"]
