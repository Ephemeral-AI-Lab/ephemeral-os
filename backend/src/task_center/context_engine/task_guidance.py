"""Registry-driven ``<Task Guidance>`` body builder.

Two terse labeled sections, no per-role prose branching:

* ``What's in context:`` — deterministic outline produced by
  :func:`render_context_outline` over the recipe's :class:`ContextPacket`.
* ``What to do:`` — single line from
  :data:`AGENT_DIRECTIVES`, keyed by the resolved agent name.

The terminal-tool block is appended by the composer (see
``task_center/agent_launch/skill_message.py:_wrap_task_guidance``), so this
builder returns only the two-section body.

Operational heuristics ("after failure, diagnose first", "treat
``<dependency>`` outputs as fixed inputs") live in role skill files, not in
this prose — the agent reads them once at boot from the skill row.

The explorer subagent has no composer involvement; it consumes
:func:`build_explorer_task_guidance` directly from
``tools/subagent/run_subagent.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from task_center.context_engine.agent_directives import AGENT_DIRECTIVES
from task_center.context_engine.context_outline import render_context_outline

if TYPE_CHECKING:  # pragma: no cover - typing-only
    from agents import AgentDefinition
    from task_center.context_engine.packet import ContextPacket
    from task_center.context_engine.scope import ContextScope


def build_task_guidance(
    *,
    agent_def: AgentDefinition,
    packet: ContextPacket,
    scope: ContextScope,  # noqa: ARG001 - dispatch signature
) -> str:
    """Return the two-section ``<Task Guidance>`` body for ``agent_def``."""
    directive = AGENT_DIRECTIVES.get(agent_def.name)
    if directive is None:
        raise KeyError(
            f"No AGENT_DIRECTIVES entry for agent {agent_def.name!r}. Add one "
            "row to backend/src/task_center/context_engine/agent_directives.py."
        )
    outline = render_context_outline(packet)
    return f"What's in context:\n{outline}\n\nWhat to do:\n- {directive}"


def build_explorer_task_guidance() -> str:
    """Identity + format prose for the explorer subagent.

    Subagents have no ContextScope and no composer involvement (isolation
    contract — see ``tools/subagent/run_subagent.py``). The explorer launches
    in the two-user-message shape by passing this prose as the spawn prompt
    and the caller's free-text task prompt as ``initial_messages[0]``.

    Returned as a plain string (no ``<Task Guidance>`` wrapping) — the
    subagent caller embeds it directly.
    """
    return (
        "# What's in context\n"
        "- Parent's user message above\n"
        "\n"
        "# What to do\n"
        f"- {AGENT_DIRECTIVES['explorer']}\n"
        "\n"
        "## Deliver\n"
        "- File paths, line numbers, specific symbols. No vague hand-waves.\n"
        "- Missing context the parent will need to act on the findings.\n"
        "- Obvious areas you skipped.\n"
        "\n"
        "## Submit\n"
        "Call `submit_exploration_result`."
    )


__all__ = [
    "build_explorer_task_guidance",
    "build_task_guidance",
]
