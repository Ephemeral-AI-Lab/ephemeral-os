"""Executor agent definition."""

from __future__ import annotations

from importlib.resources import files

from agents.types import AgentDefinition
from task_center.harness_agents.tool_surfaces import DIRECT_WORK_TOOLS


def load_system_prompt() -> str:
    """Load the canonical role-local markdown system prompt."""
    return files(__package__).joinpath("agent.md").read_text(encoding="utf-8")


EXECUTOR = AgentDefinition(
    name="executor",
    description=(
        "Owner of a code-engineering task. Implements the change directly, "
        "soft-fails when blocked, or escalates via request_plan for "
        "decomposition. Does not own research or synthesis — those belong to "
        "the planner."
    ),
    role="executor",
    agent_type="agent",
    model="inherit",
    tool_call_limit=100,
    system_prompt=load_system_prompt(),
    allowed_tools=list(DIRECT_WORK_TOOLS),
    terminals=[
        "submit_task_success",
        "submit_task_failure",
        "request_plan",
    ],
)

__all__ = ["EXECUTOR", "load_system_prompt"]
