"""Row-3 (``<Task Guidance>``) and row-4 (``Load skill:``) wrappers.

* :func:`_wrap_task_guidance` wraps role prose in ``<Task Guidance>`` and
  appends one ``<terminal_tool_selection>`` block rendered from the shared
  terminal registry. ``None`` prose is preserved as ``None``.
* :func:`build_skill_message` reads the agent's skill markdown body and
  appends an identical ``<terminal_tool_selection>`` block — byte-equal to
  the row-3 block (AC #15) because both derive from the same
  ``render_terminal_catalog(focus="selection_guidance")`` call.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing-only
    from agents import AgentDefinition


def _render_terminal_tool_selection_block(agent_def: AgentDefinition) -> str | None:
    """Render the shared ``<terminal_tool_selection>`` block.

    Returns ``None`` when the agent declares no terminals. The same string is
    appended to both row 3 and row 4 — see AC #15 (byte-equal between
    ``messages.task_guidance`` and ``messages.skill``).
    """
    if not agent_def.terminals:
        return None
    from tools._terminals.registry import render_terminal_catalog

    catalog = render_terminal_catalog(list(agent_def.terminals), focus="selection_guidance")
    return f"<terminal_tool_selection>\n{catalog}\n</terminal_tool_selection>"


def _wrap_task_guidance(
    prose: str | None,
    agent_def: AgentDefinition,
) -> str | None:
    """Wrap role prose in ``<Task Guidance>`` plus terminal selection.

    Returns ``None`` when *prose* is ``None``.
    """
    if prose is None:
        return None
    body = prose.rstrip()
    terminal_block = _render_terminal_tool_selection_block(agent_def)
    if terminal_block:
        return f"<Task Guidance>\n{body}\n\n{terminal_block}\n</Task Guidance>"
    return "<Task Guidance>\n" + body + "\n</Task Guidance>"


def build_skill_message(
    skill_path: Path | None,
    agent_def: AgentDefinition,
) -> str | None:
    """Compose the row-4 skill + ``<terminal_tool_selection>`` message.

    Returns ``None`` when no skill is declared. When a skill is declared, the
    return is the row-4 body::

        Load skill: <skill-folder-name>

        <skill>
        <frontmatter-stripped skill body>
        </skill>

        <terminal_tool_selection>
        Pick exactly one based on outcome:

        - <tool_name>: <selection_guidance>
        ...
        </terminal_tool_selection>

    The ``<terminal_tool_selection>`` block is byte-equal to the row-3 block
    produced by :func:`_wrap_task_guidance` (AC #15).
    """
    if skill_path is None:
        return None
    from config.markdown import parse_markdown_frontmatter

    raw = skill_path.read_text(encoding="utf-8")
    _, body = parse_markdown_frontmatter(raw)
    body = body.strip()
    skill_name = skill_path.parent.name

    parts = [
        f"Load skill: {skill_name}",
        "",
        "<skill>",
        body,
        "</skill>",
    ]
    terminal_block = _render_terminal_tool_selection_block(agent_def)
    if terminal_block:
        parts.extend(["", terminal_block])
    return "\n".join(parts)


__all__ = ["_wrap_task_guidance", "build_skill_message"]
