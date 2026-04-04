"""Coordinator exports."""

from ephemeralos.coordinator.agent_definitions import AgentDefinition, get_builtin_agent_definitions
from ephemeralos.coordinator.coordinator_mode import TeamRecord, TeamRegistry, get_team_registry

__all__ = [
    "AgentDefinition",
    "TeamRecord",
    "TeamRegistry",
    "get_builtin_agent_definitions",
    "get_team_registry",
]
