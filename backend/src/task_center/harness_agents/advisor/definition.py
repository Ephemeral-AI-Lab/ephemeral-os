"""Advisor agent definition (Stage 4 of the four-role roadmap)."""

from __future__ import annotations

from importlib.resources import files

from agents.types import AgentDefinition
from notification.library import make_opening_reminder


def load_system_prompt() -> str:
    """Load the canonical role-local markdown system prompt."""
    return files(__package__).joinpath("agent.md").read_text(encoding="utf-8")


def _load_distilled_rules() -> str:
    return files(__package__).joinpath("distilled_rules.md").read_text(encoding="utf-8")


ADVISOR = AgentDefinition(
    name="advisor",
    description=(
        "Second-LLM check on a high-stakes terminal proposal. Reads the "
        "calling agent's context + the proposed (terminal, input, reason) "
        "and emits accept/reject. No file or shell tools — verdict comes "
        "from the calling agent's stored evidence alone."
    ),
    role="advisor",
    agent_type="agent",
    model="inherit",
    tool_call_limit=8,
    system_prompt=load_system_prompt(),
    allowed_tools=[],
    terminals=["submit_advisor_feedback"],
    notification_rules=[
        make_opening_reminder(_load_distilled_rules()),
    ],
)

__all__ = ["ADVISOR", "load_system_prompt"]
