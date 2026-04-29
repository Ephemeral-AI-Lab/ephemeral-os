"""Evaluator agent definition."""

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


EVALUATOR = AgentDefinition(
    name="evaluator",
    description=(
        "Closure gate for a planning unit. Validates child summaries and "
        "either succeeds, hard-fails, or hands off to a recovery planner."
    ),
    role="evaluator",
    agent_type="agent",
    model="inherit",
    tool_call_limit=100,
    system_prompt=load_system_prompt(),
    allowed_tools=list(DIRECT_WORK_TOOLS),
    terminals=[
        # Stage 7 four-role rename: agent.md now directs evaluators at the
        # new ``submit_evaluation_success`` terminal. ``submit_task_success``
        # stays in the allowed list as a polymorphic backward-compat path
        # while existing tests + scripted spawns migrate.
        "submit_evaluation_success",
        "submit_task_success",
        "submit_evaluation_failure",
        "request_plan",
    ],
    notification_rules=[
        make_opening_reminder(_load_distilled_rules()),
        make_budget_warning(),
    ],
)

__all__ = ["EVALUATOR", "load_system_prompt"]
