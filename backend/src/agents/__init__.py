"""Public facade for agent definitions, validation, loading, and tracking."""

from __future__ import annotations

from .definition.loader import load_agents_dir, load_agents_tree
from .definition.registry import (
    get_definition,
    list_dispatchable_subagent_names,
    list_definitions,
    register_definition,
    unregister_definition,
)
from .definition.model import (
    AgentDefinition,
    AgentSelectionBlock,
    AgentType,
    AgentVariant,
)
from .definition.resolved_validation import validate_agent_definitions_resolved
from .definition.tool_validation import (
    AgentDefinitionValidator,
    AgentValidationResult,
)

__all__ = [
    "AgentDefinition",
    "AgentDefinitionValidator",
    "AgentSelectionBlock",
    "AgentType",
    "AgentValidationResult",
    "AgentVariant",
    "get_definition",
    "list_dispatchable_subagent_names",
    "list_definitions",
    "load_agents_dir",
    "load_agents_tree",
    "register_definition",
    "unregister_definition",
    "validate_agent_definitions_resolved",
]
