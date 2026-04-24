"""Agents module — first-class agent definitions, validation, and registry.

Import from here instead of deep paths::

    from agents import AgentDefinition, get_definition
"""

from agents.registry import (
    get_definition,
    list_definitions,
    register_definition,
    unregister_definition,
)
from agents.types import EFFORT_LEVELS, AgentDefinition

__all__ = [
    "EFFORT_LEVELS",
    "AgentDefinition",
    "get_definition",
    "list_definitions",
    "register_definition",
    "unregister_definition",
]
