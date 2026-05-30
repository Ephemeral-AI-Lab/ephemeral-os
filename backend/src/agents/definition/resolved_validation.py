"""Resolved-reference validation for registered agent definitions."""

from __future__ import annotations

from .registry import list_definitions


def validate_agent_definitions_resolved() -> None:
    """Cross-check every registered :class:`AgentDefinition`.

    Runs the row-4 terminal-silence lint over every declared skill file
    (:func:`agents.skills.validate_skill_files`). Context construction is
    role-scoped now, so there is no process-global recipe registry to validate.

    Called once at app startup after ``load_agents_tree`` so wiring mistakes
    surface before the first request.
    """
    definitions = list_definitions()
    from agents.skills import validate_skill_files

    validate_skill_files(definitions)
