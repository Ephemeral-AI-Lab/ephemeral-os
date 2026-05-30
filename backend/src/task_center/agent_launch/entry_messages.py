"""Composer output bundle."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing-only
    from agents import AgentDefinition
    from task_center.context_engine.context import AgentContext


@dataclass(frozen=True, slots=True)
class AgentEntryMessages:
    """The composer's output: everything the launcher needs.

    Field shapes:

    * ``context`` — ``<context>...</context>`` envelope around rendered role
      context.
    * ``task_guidance`` — ``<Task Guidance>...</Task Guidance>`` envelope
      around role prose; ``None`` for agents with no task-guidance builder
      (helpers/subagents).
    * ``skill`` — row-4 skill + ``<terminal_tool_selection>`` body; ``None``
      when the agent declares no skill.
    """

    agent_def: AgentDefinition
    context: str
    task_guidance: str | None
    skill: str | None
    packet: AgentContext


__all__ = ["AgentEntryMessages"]
