"""Resolved-reference validation for registered agent definitions."""

from __future__ import annotations

from .registry import list_definitions
from .model import AgentDefinition


def validate_agent_definitions_resolved() -> None:
    """Cross-check every registered :class:`AgentDefinition`.

    Raises :class:`AgentDefinitionValidationError` if any agent references an
    unregistered context recipe. Also runs the row-4 terminal-silence lint over
    every declared skill file
    (:func:`agents.skills.validate_skill_files`).

    Called once at app startup after ``load_agents_tree`` so wiring mistakes
    surface before the first request.
    """
    definitions = list_definitions()
    for definition in definitions:
        _validate_definition(definition)

    # Skill-file lint runs after cross-reference validation so the failure
    # message points at a real, resolvable definition. Lazy import avoids a
    # registry-vs-loader import cycle.
    from agents.skills import validate_skill_files

    validate_skill_files(definitions)


def _validate_definition(definition: AgentDefinition) -> None:
    from task_center import (
        AgentDefinitionValidationError,
        RecipeRegistry,
    )

    if definition.context_recipe and not RecipeRegistry.has(definition.context_recipe):
        raise AgentDefinitionValidationError(
            f"Agent {definition.name!r} declares context_recipe="
            f"{definition.context_recipe!r}, which is not registered."
        )
