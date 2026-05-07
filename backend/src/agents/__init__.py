"""Public facade for agent definitions, validation, loading, and tracking."""

from __future__ import annotations

from .loader import load_agents_dir, load_agents_tree
from .registry import (
    get_definition,
    list_dispatchable_subagent_names,
    list_definitions,
    register_definition,
    unregister_definition,
)
from .run_tracker import AgentRunTracker
from .types import (
    AgentDefinition,
    AgentSelectionBlock,
    AgentType,
    AgentVariant,
)
from .validation import (
    AgentDefinitionValidator,
    AgentValidationInput,
    AgentValidationResult,
    validate_agent_definitions_resolved,
)

__all__ = [
    "AgentDefinition",
    "AgentDefinitionValidator",
    "AgentRunTracker",
    "AgentSelectionBlock",
    "AgentType",
    "AgentValidationInput",
    "AgentValidationResult",
    "AgentVariant",
    "create_agents_router",
    "get_definition",
    "list_dispatchable_subagent_names",
    "list_definitions",
    "load_agents_dir",
    "load_agents_tree",
    "register_definition",
    "unregister_definition",
    "validate_agent_definitions_resolved",
]


def __getattr__(name: str) -> object:
    if name == "create_agents_router":
        from .api.router import create_agents_router

        return create_agents_router
    raise AttributeError(name)
