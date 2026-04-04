"""Backward compatibility shim — all agent code now lives in ephemeralos.agents.*."""

# Re-export everything from the new agents module so existing imports keep working.
from ephemeralos.agents import *  # noqa: F401, F403
from ephemeralos.agents import (  # noqa: F811  explicit re-exports for type checkers
    AGENT_COLORS,
    EFFORT_LEVELS,
    ISOLATION_MODES,
    MEMORY_SCOPES,
    PERMISSION_MODES,
    AgentDefinition,
    filter_agents_by_mcp_requirements,
    get_agent_definition,
    get_all_agent_definitions,
    get_builtin_agent_definitions,
    get_definition,
    has_required_mcp_servers,
    initialize_builtin_definitions,
    list_definitions,
    load_agents_dir,
    register_definition,
    unregister_definition,
)

__all__ = [
    "AGENT_COLORS",
    "EFFORT_LEVELS",
    "ISOLATION_MODES",
    "MEMORY_SCOPES",
    "PERMISSION_MODES",
    "AgentDefinition",
    "filter_agents_by_mcp_requirements",
    "get_agent_definition",
    "get_all_agent_definitions",
    "get_builtin_agent_definitions",
    "get_definition",
    "has_required_mcp_servers",
    "initialize_builtin_definitions",
    "list_definitions",
    "load_agents_dir",
    "register_definition",
    "unregister_definition",
]
