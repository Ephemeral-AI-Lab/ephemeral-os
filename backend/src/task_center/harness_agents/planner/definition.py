"""Planner agent definition."""

from __future__ import annotations

from importlib.resources import files

from agents.types import AgentDefinition
from notification.library import make_budget_warning, make_opening_reminder
from task_center.harness_agents.tool_surfaces import PLANNER_TOOLS


def load_system_prompt() -> str:
    """Load the canonical role-local markdown system prompt."""
    return files(__package__).joinpath("agent.md").read_text(encoding="utf-8")


def _load_distilled_rules() -> str:
    return files(__package__).joinpath("distilled_rules.md").read_text(encoding="utf-8")


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
    allowed_tools=list(PLANNER_TOOLS),
    terminals=["submit_plan_handoff"],
    notification_rules=[
        make_opening_reminder(_load_distilled_rules()),
        make_budget_warning(),
    ],
)

__all__ = ["PLANNER", "load_system_prompt"]
