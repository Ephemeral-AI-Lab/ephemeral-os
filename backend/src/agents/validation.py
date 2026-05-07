"""Validation service for config-backed agent definitions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel, Field

from .registry import (
    RESERVED_BUILTIN_AGENT_NAMES,
    get_definition,
    list_definitions,
)
from .types import AgentDefinition

if TYPE_CHECKING:
    from task_center.agent_launch.predicates import PredicateRegistry as _PR
    from task_center.context_engine.recipes_registry import (
        RecipeRegistry as _RR,
    )
    from tools import ToolRegistry


class AgentValidationInput(Protocol):
    """Definition fields required by ``AgentDefinitionValidator``."""

    name: str
    allowed_tools: list[str]
    terminals: list[str]


class AgentValidationResult(BaseModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class AgentDefinitionValidator:
    """Validate agent definition references without persisting definitions."""

    def __init__(self, tool_registry: ToolRegistry | None) -> None:
        self._tool_registry = tool_registry

    def validate(self, defn: AgentValidationInput) -> AgentValidationResult:
        errors: list[str] = []
        warnings: list[str] = []

        if defn.name in RESERVED_BUILTIN_AGENT_NAMES:
            errors.append(f"Agent name is reserved for a builtin runtime agent: {defn.name}")

        requested_tools = self._collect_requested_tools(defn)
        if requested_tools:
            known_tools = self._resolve_all_tool_names()
            unknown_tools = sorted(requested_tools - known_tools)
            for tool_name in unknown_tools:
                errors.append(f"Unknown tool: {tool_name}")

        return AgentValidationResult(valid=len(errors) == 0, errors=errors, warnings=warnings)

    def _resolve_all_tool_names(self) -> set[str]:
        from tools import collect_tool_catalog

        return {
            entry.name
            for entry in collect_tool_catalog(
                self._tool_registry,
                include_runtime_tools=True,
            )
        }

    @staticmethod
    def _collect_requested_tools(defn: AgentValidationInput) -> set[str]:
        return set(defn.allowed_tools) | set(defn.terminals)


def validate_agent_definitions_resolved(
    *,
    predicate_registry: type["_PR"] | None = None,
    recipe_registry: type["_RR"] | None = None,
) -> None:
    """Cross-check every registered :class:`AgentDefinition`.

    Raises :class:`AgentDefinitionValidationError` if any agent references an
    unregistered predicate / recipe / variant target, or declares a variant
    target that itself has variants (chaining is forbidden).

    Called once at app startup after ``load_agents_tree`` so wiring mistakes
    surface before the first request.
    """
    from task_center.agent_launch.predicates import (
        PredicateRegistry as DefaultPredicateRegistry,
    )
    from task_center.context_engine.errors import (
        AgentDefinitionValidationError,
    )
    from task_center.context_engine.recipes_registry import (
        RecipeRegistry as DefaultRecipeRegistry,
    )

    predicates = predicate_registry or DefaultPredicateRegistry
    recipes = recipe_registry or DefaultRecipeRegistry

    for definition in list_definitions():
        _validate_definition(
            definition,
            predicates=predicates,
            recipes=recipes,
            error_cls=AgentDefinitionValidationError,
        )


def _validate_definition(
    definition: AgentDefinition,
    *,
    predicates: type["_PR"],
    recipes: type["_RR"],
    error_cls: type[Exception],
) -> None:
    if definition.context_recipe and not recipes.has(definition.context_recipe):
        raise error_cls(
            f"Agent {definition.name!r} declares context_recipe="
            f"{definition.context_recipe!r}, which is not registered."
        )
    for variant in definition.variants:
        if not predicates.has(variant.when):
            raise error_cls(
                f"Agent {definition.name!r} variant references unknown "
                f"predicate {variant.when!r}."
            )
        target = get_definition(variant.use)
        if target is None:
            raise error_cls(
                f"Agent {definition.name!r} variant points to unknown agent "
                f"{variant.use!r}."
            )
        if target.variants:
            raise error_cls(
                f"Agent {definition.name!r} variant target {target.name!r} "
                "declares its own variants — chaining is forbidden."
            )
        if target.context_recipe and not recipes.has(target.context_recipe):
            raise error_cls(
                f"Variant target {target.name!r} declares context_recipe="
                f"{target.context_recipe!r}, which is not registered."
            )
