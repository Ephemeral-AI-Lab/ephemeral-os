"""Tools for enumerating specialist and coordinator agents."""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel

from ephemeralos.tools.base import BaseTool, ToolExecutionContext, ToolResult

logger = logging.getLogger(__name__)

# Roles that belong to the coordination pipeline, not specialist work
PLANNER_ROLES: set[str] = {
    "coordinator",
    "explorer",
    "analyzer",
    "synthesizer",
    "task_planner",
    "replanner",
}


class _EmptyInput(BaseModel):
    """No parameters required."""


def _render_agent_listing(
    candidate_names: list[str],
    context: ToolExecutionContext,
    *,
    include_roles: set[str] | None = None,
    exclude_roles: set[str] | None = None,
    include_role_field: bool = False,
) -> str:
    """Render a filtered JSON agent listing."""
    get_agent_metadata = context.metadata.get("get_agent_metadata")
    if get_agent_metadata is None:
        return json.dumps({"error": "Agent metadata service not available"})

    agents: list[dict[str, Any]] = []
    for name in candidate_names:
        try:
            meta = get_agent_metadata(name)
        except Exception as e:
            logger.warning("Failed to get metadata for agent '%s': %s", name, e)
            continue

        role = meta.get("role")
        if include_roles is not None and role not in include_roles:
            continue
        if exclude_roles is not None and role in exclude_roles:
            continue
        normalized_role = str(role or "").strip().lower()
        normalized_name = str(name or "").strip().lower()
        if include_roles is None and (
            normalized_role == "replanner"
            or "replanner" in normalized_name
            or "replan" in normalized_name
        ):
            continue

        entry: dict[str, Any] = {
            "name": meta.get("name", name),
            "description": meta.get("description", ""),
        }
        if include_role_field:
            entry["role"] = role
        agents.append(entry)

    return json.dumps(agents, indent=2)


class ListSpecialistAgentsTool(BaseTool):
    """List specialist agents available for task assignment."""

    name = "list_specialist_agents"
    description = (
        "List available specialist agents that can be assigned tasks. "
        "Returns a JSON array of agent objects with name and description. "
        "Coordinator and planner roles are excluded."
    )
    input_model = _EmptyInput

    def __init__(self, *, team_agent_names: list[str] | None = None) -> None:
        self._team_agent_names = team_agent_names

    async def execute(self, arguments: _EmptyInput, context: ToolExecutionContext) -> ToolResult:
        list_agents = context.metadata.get("list_agents")
        candidates = (
            self._team_agent_names
            if self._team_agent_names is not None
            else (list_agents() if list_agents else [])
        )
        output = _render_agent_listing(
            list(candidates), context, exclude_roles=PLANNER_ROLES
        )
        return ToolResult(output=output)


class ListCoordinatorAgentsTool(BaseTool):
    """List coordination pipeline agents."""

    name = "list_coordinator_agents"
    description = (
        "List coordination-owned agents (coordinator, explorer, analyzer, "
        "synthesizer, task_planner) with name, description, and role."
    )
    input_model = _EmptyInput

    async def execute(self, arguments: _EmptyInput, context: ToolExecutionContext) -> ToolResult:
        list_agents = context.metadata.get("list_agents")
        candidates = list_agents() if list_agents else []
        output = _render_agent_listing(
            candidates, context, include_roles=PLANNER_ROLES, include_role_field=True
        )
        return ToolResult(output=output)


class ListAvailableAgentsTool(BaseTool):
    """List all assignable agents (alias for list_specialist_agents)."""

    name = "list_available_agents"
    description = (
        "List all assignable specialist agents. "
        "Excludes coordinator pipeline and replanner roles."
    )
    input_model = _EmptyInput

    def __init__(self, *, team_agent_names: list[str] | None = None) -> None:
        self._team_agent_names = team_agent_names

    async def execute(self, arguments: _EmptyInput, context: ToolExecutionContext) -> ToolResult:
        list_agents = context.metadata.get("list_agents")
        candidates = (
            self._team_agent_names
            if self._team_agent_names is not None
            else (list_agents() if list_agents else [])
        )
        output = _render_agent_listing(
            list(candidates), context, exclude_roles=PLANNER_ROLES
        )
        return ToolResult(output=output)
