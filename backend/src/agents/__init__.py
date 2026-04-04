"""Agents module — first-class agent definitions, builder, and registry.

Import from here instead of deep paths:

    from ephemeralos.agents import AgentDefinition, get_definition, AgentBuilderService
"""

from ephemeralos.agents.types import (
    AGENT_COLORS,
    EFFORT_LEVELS,
    ISOLATION_MODES,
    MEMORY_SCOPES,
    PERMISSION_MODES,
    AgentDefinition,
)
from ephemeralos.agents.registry import (
    get_definition,
    initialize_builtin_definitions,
    list_definitions,
    register_definition,
    unregister_definition,
)
from ephemeralos.agents.builtins import get_builtin_agent_definitions
from ephemeralos.agents.loader import (
    filter_agents_by_mcp_requirements,
    get_agent_definition,
    get_all_agent_definitions,
    has_required_mcp_servers,
    load_agents_dir,
)

__all__ = [
    # Types & constants
    "AgentDefinition",
    "AGENT_COLORS",
    "EFFORT_LEVELS",
    "ISOLATION_MODES",
    "MEMORY_SCOPES",
    "PERMISSION_MODES",
    # Registry
    "register_definition",
    "unregister_definition",
    "get_definition",
    "list_definitions",
    "initialize_builtin_definitions",
    # Builtins
    "get_builtin_agent_definitions",
    # Loader
    "get_agent_definition",
    "get_all_agent_definitions",
    "load_agents_dir",
    "has_required_mcp_servers",
    "filter_agents_by_mcp_requirements",
]
