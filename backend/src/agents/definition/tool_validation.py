"""Tool-surface validation for config-backed agent definitions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel, Field

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
    """Validate agent tool references without persisting definitions."""

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
