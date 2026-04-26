"""Validation service for config-backed agent definitions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel, Field

from agents.registry import RESERVED_BUILTIN_AGENT_NAMES

if TYPE_CHECKING:
    from agents.types import ModeDefinition
    from tools.core.base import ToolRegistry


class AgentValidationInput(Protocol):
    """Definition fields required by ``AgentDefinitionValidator``."""

    name: str
    modes: list["ModeDefinition"]


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
        from tools.core.catalog import collect_tool_catalog

        return {
            entry.name
            for entry in collect_tool_catalog(
                self._tool_registry,
                include_runtime_tools=True,
            )
        }

    @staticmethod
    def _collect_requested_tools(defn: AgentValidationInput) -> set[str]:
        names: set[str] = set()
        for mode in defn.modes:
            names.update(mode.allowed_tools)
            names.update(mode.terminals)
            if mode.entry_tool:
                names.add(mode.entry_tool)
        return names
