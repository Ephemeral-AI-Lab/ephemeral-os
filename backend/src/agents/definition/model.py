"""Agent definition model and constants."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)


class AgentType(StrEnum):
    """Runtime class of an agent profile."""

    AGENT = "agent"
    SUBAGENT = "subagent"


class AgentRole(StrEnum):
    """Canonical category of an agent profile.

    Pure identity: read as the ``metadata["role"]`` audit tag (emitted by
    ``factory.py`` and ``run_subagent.py``) and by the planner-submission gate.
    ``GENERATOR`` covers the executor profile; ``REDUCER`` digests/gates;
    ``HELPER`` is the advisor, ``SUBAGENT`` is the explorer.
    """

    ROOT = "root"
    PLANNER = "planner"
    GENERATOR = "generator"
    REDUCER = "reducer"
    HELPER = "helper"
    SUBAGENT = "subagent"


class AgentDefinition(BaseModel):
    """Full agent definition with all configuration fields."""

    # --- required ---
    name: str
    description: str

    # --- prompt ---
    system_prompt: str | None = None

    # --- model ---
    model: str | None = None

    # --- agent loop control ---
    # Per-ephemeral-run cap on tool dispatches. Required and positive.
    # Each ``EphemeralAgent`` spawn starts with a fresh counter, so
    # nested ``run_subagent`` calls have independent budgets and the
    # caller's counter is untouched. The loop's hard ceiling is
    # ``ceil(1.5 * tool_call_limit)``.
    tool_call_limit: int = Field(..., gt=0)

    # --- role ---
    # Canonical category of this profile (planner / generator / reducer /
    # helper / subagent). The planner-submission gate reads this, and audit
    # consumers read ``role.value`` through the ``metadata["role"]`` key
    # emitted by ``factory.py`` and ``run_subagent.py``. Profile MDs MUST
    # declare ``role:`` in frontmatter — the loader rejects MDs that omit it.
    # The Pydantic default exists only so test fixtures that build
    # ``AgentDefinition`` directly stay terse; production agents always go
    # through the loader gate.
    role: AgentRole = AgentRole.GENERATOR

    # --- agent type: regular agent or subagent (worker) ---
    agent_type: AgentType = AgentType.AGENT

    # --- run tool surface ---
    # Tools the agent may call during a run. The agent's tool registry is
    # filtered to ``allowed_tools ∪ terminals``; the LLM only sees those.
    allowed_tools: list[str] = Field(default_factory=list)
    # Terminal tools — calling any of these ends the query loop. Required and
    # non-empty: every agent must declare at least one terminal-capable tool.
    terminals: list[str] = Field(..., min_length=1)
    # Declarative notification trigger ids resolved into NotificationRule
    # instances by runtime-specific launch code.
    notification_triggers: list[str] = Field(default_factory=list)

    # --- skill (Round 3) ---
    # Absolute path to the agent's workflow SKILL.md, resolved by the loader
    # from the relative ``skill:`` frontmatter field. ``None`` when no skill is
    # declared. Skill-equipped agents get row 4 (skill +
    # terminal_tool_selection) composed at launch.
    skill: Path | None = None

    # --- context engine (AgentEntryComposer) ---
    # Recipe id resolved at compose time. Required when the agent is launched
    # via ``AgentEntryComposer``; helper / subagent definitions that pre-date
    # the context engine may keep this null.
    context_recipe: str | None = None

    model_config = ConfigDict(
        extra="forbid",
    )

    @field_validator("tool_call_limit", mode="before")
    @classmethod
    def _coerce_int(cls, v: Any) -> Any:
        if isinstance(v, int):
            return v
        try:
            return int(v)
        except (TypeError, ValueError):
            return v

    @field_validator("terminals")
    @classmethod
    def _check_terminals(cls, terminals: list[str]) -> list[str]:
        cleaned = [terminal for terminal in terminals if terminal.strip()]
        if not cleaned:
            raise ValueError("AgentDefinition.terminals must be non-empty")
        return cleaned

    @field_validator("notification_triggers")
    @classmethod
    def _check_notification_triggers(cls, triggers: list[str]) -> list[str]:
        return [trigger for trigger in triggers if trigger.strip()]
