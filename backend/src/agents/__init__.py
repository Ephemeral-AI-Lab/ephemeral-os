"""Agents module — first-class agent definitions, builder, and registry.

Import from here instead of deep paths:

    from agents import AgentDefinition, get_definition, AgentBuilderService
"""

from agents.types import (
    EFFORT_LEVELS,
    AgentDefinition,
)
from agents.registry import (
    get_definition,
    initialize_builtin_definitions,
    list_definitions,
    register_definition,
    unregister_definition,
)
from agents.builtins import get_builtin_agent_definitions
from agents.loader import (
    get_agent_definition,
    get_all_agent_definitions,
    load_agents_dir,
)

__all__ = [
    # Types & constants
    "AgentDefinition",
    "EFFORT_LEVELS",
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
]
