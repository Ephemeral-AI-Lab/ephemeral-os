"""Explorer agent definition."""

from __future__ import annotations

from importlib.resources import files

from agents.types import AgentDefinition
from task_center.harness_agents.tool_surfaces import READ_ONLY_INVESTIGATION_TOOLS


def load_system_prompt() -> str:
    """Load the canonical role-local markdown system prompt."""
    return files(__package__).joinpath("agent.md").read_text(encoding="utf-8")


EXPLORER = AgentDefinition(
    name="explorer",
    description=(
        "Read-only exploration subagent. Investigates a focused question and "
        "returns findings to the dispatching parent agent."
    ),
    role="explorer",
    agent_type="subagent",
    model="inherit",
    tool_call_limit=50,
    system_prompt=load_system_prompt(),
    allowed_tools=list(READ_ONLY_INVESTIGATION_TOOLS),
    terminals=["submit_exploration_result"],
)


__all__ = ["EXPLORER", "load_system_prompt"]
