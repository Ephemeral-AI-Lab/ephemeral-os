"""Verifier agent definition."""

from __future__ import annotations

from importlib.resources import files

from agents.types import AgentDefinition
from notification.library import make_budget_warning, make_opening_reminder
from task_center.harness_agents.tool_surfaces import DIRECT_WORK_TOOLS


def load_system_prompt() -> str:
    """Load the canonical role-local markdown system prompt."""
    return files(__package__).joinpath("agent.md").read_text(encoding="utf-8")


def _load_distilled_rules() -> str:
    return files(__package__).joinpath("distilled_rules.md").read_text(encoding="utf-8")


VERIFIER = AgentDefinition(
    name="verifier",
    description=(
        "Mid-graph node-scoped verifier. Validates DAG dependencies against "
        "this node's verification specification. Scoped to one node — no "
        "root_goal, no plan summary, no graph-closure decisions. Failure "
        "spawns a fix-executor; success unblocks dependents."
    ),
    role="verifier",
    agent_type="agent",
    model="inherit",
    tool_call_limit=100,
    system_prompt=load_system_prompt(),
    allowed_tools=list(DIRECT_WORK_TOOLS),
    terminals=[
        "submit_verification_success",
        "submit_verification_failure",
    ],
    notification_rules=[
        make_opening_reminder(_load_distilled_rules()),
        make_budget_warning(),
    ],
)

__all__ = ["VERIFIER", "load_system_prompt"]
