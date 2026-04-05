"""Tools for enumerating available agents."""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from tools.base import BaseTool, ToolExecutionContext, ToolResult

logger = logging.getLogger(__name__)


@runtime_checkable
class AgentMetadataFn(Protocol):
    """Returns metadata dict for a named agent."""

    def __call__(self, name: str) -> dict[str, Any]: ...


@runtime_checkable
class ListAgentsFn(Protocol):
    """Returns list of agent names."""

    def __call__(self) -> list[str]: ...


class ListAgentsInput(BaseModel):
    """Arguments for listing agents."""

    role_filter: str | None = Field(
        default=None,
        description="Optional role to filter by. Only agents with this role are returned.",
    )
    exclude_roles: list[str] | None = Field(
        default=None,
        description="Optional roles to exclude from results.",
    )


class ListAgentsTool(BaseTool):
    """List available agents with optional role filtering."""

    name = "list_agents"
    description = (
        "List available agents. Optionally filter by role or exclude specific roles. "
        "Returns a JSON array of agent objects with name, description, and role."
    )
    input_model = ListAgentsInput

    def __init__(self, *, agent_names: list[str] | None = None) -> None:
        self._agent_names = agent_names

    async def execute(
        self, arguments: ListAgentsInput, context: ToolExecutionContext
    ) -> ToolResult:
        get_metadata: AgentMetadataFn | None = context.metadata.get("get_agent_metadata")
        list_agents_fn: ListAgentsFn | None = context.metadata.get("list_agents")

        if get_metadata is None:
            return ToolResult(
                output=json.dumps({"error": "Agent metadata service not available"}),
                is_error=True,
            )

        candidates = (
            self._agent_names
            if self._agent_names is not None
            else (list_agents_fn() if list_agents_fn else [])
        )

        include_role = arguments.role_filter
        exclude_set = set(arguments.exclude_roles) if arguments.exclude_roles else None

        agents: list[dict[str, Any]] = []
        for name in candidates:
            try:
                meta = get_metadata(name)
            except Exception as e:
                logger.warning("Failed to get metadata for agent '%s': %s", name, e)
                continue

            role = meta.get("role")
            if include_role is not None and role != include_role:
                continue
            if exclude_set is not None and role in exclude_set:
                continue

            agents.append({
                "name": meta.get("name", name),
                "description": meta.get("description", ""),
                "role": role,
            })

        return ToolResult(output=json.dumps(agents, indent=2))
