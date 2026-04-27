"""Planner agent definition."""

from __future__ import annotations

from importlib.resources import files

from agents.types import AgentDefinition, ModeDefinition
from task_center.harness_agents.tool_surfaces import PLANNER_TOOLS


def load_system_prompt() -> str:
    """Load the canonical role-local markdown system prompt."""
    return files(__package__).joinpath("agent.md").read_text(encoding="utf-8")


PLANNER = AgentDefinition(
    name="planner",
    description=(
        "Read-only planner with scout dispatch. Decomposes a parent goal into "
        "a recursive DAG plan with an evaluator gate."
    ),
    role="planner",
    agent_type="agent",
    model="inherit",
    tool_call_limit=100,
    system_prompt=load_system_prompt(),
    modes=[
        ModeDefinition(
            name="direct",
            is_default=True,
            allowed_tools=list(PLANNER_TOOLS),
            terminals=["submit_plan_handoff"],
        ),
    ],
)

__all__ = ["PLANNER", "load_system_prompt"]
