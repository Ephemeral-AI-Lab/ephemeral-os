"""Resolved-reference validation for registered agent definitions."""

from __future__ import annotations

from task_center.context_engine.engine import validate_context_recipe
from task_center.context_engine.exceptions import (
    AgentDefinitionValidationError,
    ContextEngineError,
)

from .model import AgentDefinition, AgentRole
from .registry import list_definitions

_CONTEXT_ENGINE_ROLES = frozenset(
    (AgentRole.PLANNER, AgentRole.GENERATOR, AgentRole.REDUCER)
)


def validate_agent_definitions_resolved() -> None:
    """Cross-check every registered :class:`AgentDefinition`.

    Runs the row-4 terminal-silence lint over every declared skill file
    (:func:`agents.skills.validate_skill_files`) and checks any declared
    ``context_recipe`` against the definition's role-scoped context builder.

    Called once at app startup after ``load_agents_tree`` so wiring mistakes
    surface before the first request.
    """
    definitions = list_definitions()
    for definition in definitions:
        _validate_context_recipe(definition)

    from agents.skills import validate_skill_files

    validate_skill_files(definitions)


def _validate_context_recipe(definition: AgentDefinition) -> None:
    recipe = definition.context_recipe
    if recipe is None:
        return
    if definition.role not in _CONTEXT_ENGINE_ROLES:
        raise AgentDefinitionValidationError(
            f"Agent {definition.name!r} declares context_recipe {recipe!r}, "
            f"but role {definition.role.value!r} has no context builder."
        )
    try:
        validate_context_recipe(recipe, definition.role.value)
    except ContextEngineError as exc:
        raise AgentDefinitionValidationError(
            f"Agent {definition.name!r} has invalid context_recipe {recipe!r}: {exc}"
        ) from exc
