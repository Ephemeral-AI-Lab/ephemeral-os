"""Built-in agent definitions.

All agents are now DB-seeded or user-defined. This module provides
the interface for backwards compatibility but returns an empty list.
"""

from __future__ import annotations

from agents.types import AgentDefinition


def get_builtin_agent_definitions() -> list[AgentDefinition]:
    """Return built-in agent definitions (none — agents live in DB/user files)."""
    return []
