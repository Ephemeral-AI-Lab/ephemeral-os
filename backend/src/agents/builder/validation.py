"""Validation service for agent definitions."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from agents.registry import RESERVED_BUILTIN_AGENT_NAMES
from agents.types import EFFORT_LEVELS
from agents.api.schemas import AgentValidationResult

if TYPE_CHECKING:
    from agents.api.schemas import AgentDefinitionCreate, AgentDefinitionUpdate
    from tools.core.base import ToolRegistry

logger = logging.getLogger(__name__)


class AgentDefinitionValidator:
    """Validates that agent definition references are resolvable."""

    def __init__(self, tool_registry: ToolRegistry | None) -> None:
        self._tool_registry = tool_registry

    def validate(self, defn: AgentDefinitionCreate | AgentDefinitionUpdate) -> AgentValidationResult:
        errors: list[str] = []
        warnings: list[str] = []

        name = getattr(defn, "name", None)
        if isinstance(name, str) and name in RESERVED_BUILTIN_AGENT_NAMES:
            errors.append(f"Agent name is reserved for a builtin runtime agent: {name}")

        requested_tools = getattr(defn, "tools", None)
        if requested_tools:
            known_tools = self._resolve_all_tool_names()
            unknown_tools = sorted(set(requested_tools) - known_tools)
            for tool_name in unknown_tools:
                errors.append(f"Unknown tool: {tool_name}")

        effort = getattr(defn, "effort", None)
        if effort is not None and effort not in EFFORT_LEVELS:
            errors.append(f"Invalid effort: {effort}")

        return AgentValidationResult(valid=len(errors) == 0, errors=errors, warnings=warnings)

    def _resolve_all_tool_names(self) -> set[str]:
        from tools.core.factory import list_available_tools

        names = set(list_available_tools())
        return names
