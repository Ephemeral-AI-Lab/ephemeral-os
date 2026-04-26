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
from agents.types import AgentDefinition, ModeDefinition

__all__ = [
    "AgentDefinition",
    "ModeDefinition",
    "get_definition",
    "list_definitions",
    "register_definition",
    "unregister_definition",
]
