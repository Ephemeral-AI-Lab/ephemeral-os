"""Resolved-reference validation for registered agent definitions."""

from __future__ import annotations

from .registry import get_definition, list_definitions
from .model import AgentDefinition


def validate_agent_definitions_resolved() -> None:
    """Cross-check every registered :class:`AgentDefinition`.

    Raises :class:`AgentDefinitionValidationError` if any agent references an
    unregistered predicate / recipe / variant target, or declares a variant
    target that itself has variants (chaining is forbidden).

    Called once at app startup after ``load_agents_tree`` so wiring mistakes
    surface before the first request.
    """
    for definition in list_definitions():
        _validate_definition(definition)


def _validate_definition(definition: AgentDefinition) -> None:
    from task_center.api import (
        AgentDefinitionValidationError,
        PredicateRegistry,
        RecipeRegistry,
    )

    if definition.context_recipe and not RecipeRegistry.has(definition.context_recipe):
        raise AgentDefinitionValidationError(
            f"Agent {definition.name!r} declares context_recipe="
            f"{definition.context_recipe!r}, which is not registered."
        )
    for variant in definition.variants:
        if not PredicateRegistry.has(variant.when):
            raise AgentDefinitionValidationError(
                f"Agent {definition.name!r} variant references unknown "
                f"predicate {variant.when!r}."
            )
        target = get_definition(variant.use)
        if target is None:
            raise AgentDefinitionValidationError(
                f"Agent {definition.name!r} variant points to unknown agent "
                f"{variant.use!r}."
            )
        if target.variants:
            raise AgentDefinitionValidationError(
                f"Agent {definition.name!r} variant target {target.name!r} "
                "declares its own variants — chaining is forbidden."
            )
        if target.context_recipe and not RecipeRegistry.has(target.context_recipe):
            raise AgentDefinitionValidationError(
                f"Variant target {target.name!r} declares context_recipe="
                f"{target.context_recipe!r}, which is not registered."
            )
