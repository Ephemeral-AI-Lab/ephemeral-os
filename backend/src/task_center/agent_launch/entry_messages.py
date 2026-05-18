"""Composer output bundle (formerly ``LaunchBundle``).

The composer's output: agent definition, rendered context envelope (or empty
string for entry-shape recipes), optional task-guidance and skill rows, and
the packet itself for downstream persistence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from task_center.context_engine.packet import ContextPacket

if TYPE_CHECKING:  # pragma: no cover - typing-only
    from agents import AgentDefinition


@dataclass(frozen=True, slots=True)
class AgentEntryMessages:
    """The composer's output: everything the launcher needs.

    Field shapes:

    * ``context`` — ``<context>...</context>\n`` envelope around rendered
      blocks, or ``""`` for an empty packet (entry-shape recipes).
    * ``task_guidance`` — ``<Task Guidance>...</Task Guidance>`` envelope
      around role prose; ``None`` for agents with no task-guidance builder
      (entry_executor, helpers/subagents).
    * ``skill`` — row-4 skill + ``<terminal_tool_selection>`` body; ``None``
      when the agent declares no skill.
    """

    agent_def: AgentDefinition
    context: str
    task_guidance: str | None
    skill: str | None
    packet: ContextPacket
    context_packet_id: str | None


__all__ = ["AgentEntryMessages"]
