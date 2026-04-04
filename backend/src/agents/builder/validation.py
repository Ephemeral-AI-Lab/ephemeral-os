"""Validation service for agent definitions."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ephemeralos.agents.types import AGENT_COLORS, EFFORT_LEVELS, MEMORY_SCOPES, PERMISSION_MODES
from ephemeralos.agents.api.schemas import AgentValidationResult

if TYPE_CHECKING:
    from ephemeralos.agents.api.schemas import AgentDefinitionCreate, AgentDefinitionUpdate
    from ephemeralos.tools.base import ToolRegistry

logger = logging.getLogger(__name__)


class AgentDefinitionValidator:
    """Validates that agent definition references are resolvable."""

    def __init__(self, tool_registry: "ToolRegistry | None") -> None:
        self._tool_registry = tool_registry

    def validate(self, defn: "AgentDefinitionCreate | AgentDefinitionUpdate") -> AgentValidationResult:
        errors: list[str] = []
        warnings: list[str] = []

        tools = getattr(defn, "tools", None)
        if tools and self._tool_registry:
            for t in tools:
                if t != "*" and self._tool_registry.get(t) is None:
                    warnings.append(f"Unknown tool: {t}")

        toolkits = getattr(defn, "toolkits", None)
        if toolkits:
            from ephemeralos.toolkits.factory import has_factory  # noqa: PLC0415
            for tk in toolkits:
                if not has_factory(tk):
                    errors.append(f"Unknown toolkit factory: {tk}")

        effort = getattr(defn, "effort", None)
        if effort is not None and effort not in EFFORT_LEVELS:
            errors.append(f"Invalid effort: {effort}")

        color = getattr(defn, "color", None)
        if color is not None and color not in AGENT_COLORS:
            errors.append(f"Invalid color: {color}")

        permission_mode = getattr(defn, "permission_mode", None)
        if permission_mode is not None and permission_mode not in PERMISSION_MODES:
            errors.append(f"Invalid permission_mode: {permission_mode}")

        memory = getattr(defn, "memory", None)
        if memory is not None and memory not in MEMORY_SCOPES:
            errors.append(f"Invalid memory scope: {memory}")

        return AgentValidationResult(valid=len(errors) == 0, errors=errors, warnings=warnings)
