"""Validation service for config-backed agent definitions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel, Field

from .registry import get_definition, list_definitions
from .types import AgentDefinition

if TYPE_CHECKING:
    from tools import ToolRegistry


class _AgentValidationInput(Protocol):
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

    def validate(self, defn: _AgentValidationInput) -> AgentValidationResult:
        errors: list[str] = []

        requested_tools = self._collect_requested_tools(defn)
        if requested_tools:
            known_tools = self._resolve_all_tool_names()
            unknown_tools = sorted(requested_tools - known_tools)
            for tool_name in unknown_tools:
                errors.append(f"Unknown tool: {tool_name}")

        return AgentValidationResult(valid=len(errors) == 0, errors=errors)

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
    def _collect_requested_tools(defn: _AgentValidationInput) -> set[str]:
        return set(defn.allowed_tools) | set(defn.terminals)


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
    from task_center.api import AgentDefinitionValidationError, PredicateRegistry
    from task_center.api import RecipeRegistry

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
