"""Build launch-time ``<Task Guidance>`` bodies for TaskCenter main agents.

The context engine owns packets and outlines. This module owns row-3 prose:
the context outline plus one role directive for agent names that should receive
a ``<Task Guidance>`` launch row. ``skill_message._wrap_task_guidance`` adds
the envelope and terminal-selection block.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from task_center.context_engine.context_outline import render_context_outline

if TYPE_CHECKING:  # pragma: no cover - typing-only
    from agents import AgentDefinition
    from task_center.context_engine.packet import ContextPacket
    from task_center.context_engine.scope import ContextScope


TASK_GUIDANCE_DIRECTIVES: dict[str, str] = {
    "planner": "Plan for <iteration_goal>.",
    "executor": "Complete <assigned_task>.",
    "reducer": "Digest your <needs> and gate against <assigned_prompt>.",
}


def build_launch_task_guidance(
    *,
    agent_def: AgentDefinition,
    packet: ContextPacket,
    scope: ContextScope,  # noqa: ARG001 - common launch-builder signature
) -> str | None:
    """Return row-3 prose for ``agent_def``, or ``None`` when no row is emitted."""
    if agent_def.name not in TASK_GUIDANCE_DIRECTIVES:
        return None
    return build_task_guidance(agent_def=agent_def, packet=packet, scope=scope)


def build_task_guidance(
    *,
    agent_def: AgentDefinition,
    packet: ContextPacket,
    scope: ContextScope,  # noqa: ARG001 - common launch-builder signature
) -> str:
    """Return the two-section ``<Task Guidance>`` body for a supported agent."""
    directive = TASK_GUIDANCE_DIRECTIVES.get(agent_def.name)
    if directive is None:
        raise KeyError(
            f"No task guidance directive for agent {agent_def.name!r}. Add one "
            "row to backend/src/task_center/agent_launch/task_guidance.py."
        )
    outline = render_context_outline(packet)
    return f"What's in context:\n{outline}\n\nWhat to do:\n- {directive}"


__all__ = [
    "TASK_GUIDANCE_DIRECTIVES",
    "build_launch_task_guidance",
    "build_task_guidance",
]
