"""Agent-loop tuning knobs.

`tool_call_limit` and `max_tolerance_after_max_tool_call` are per-agent
(declared in profile MDs and `AgentDefinition`) — only the global reminder
cadence lives here. See RALPLAN_agent_loop_termination.md §9.
"""

from __future__ import annotations

from pydantic import Field

from config.base import ModuleConfigBase


class EngineConfig(ModuleConfigBase):
    """Engine-wide agent-loop tuning."""

    budget_overflow_reminder_every: int = Field(default=5, ge=1)
