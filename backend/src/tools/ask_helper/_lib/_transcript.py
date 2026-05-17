"""Parent-transcript builder for transcript-mode helpers (resolver).

Reads the parent agent's ``conversation_messages`` from execution metadata
and renders the last N messages as a compact markdown block the helper can
consult as ``# Parent transcript``. Two-stage filter (see project memory
``feedback_helper_transcript_filter.md``):

1. Drop ``role == "system"`` defensively (Anthropic's message list contains
   only ``user`` and ``assistant`` roles, but a hand-built fixture or future
   provider variant may inject system messages — silently dropping is safer
   than crashing).
2. Drop the FIRST user message (the helper's own spawn prompt, which the
   helper already sees as user-msg 1 of its own run). Subsequent user-role
   messages are typically ``tool_result`` wrappers — those are preserved.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from task_center.context_engine.packet import (
    ContextBlock,
    ContextPriority,
)

logger = logging.getLogger(__name__)


MAX_TRANSCRIPT_MESSAGES = 40
MAX_TOOL_RESULT_CHARS = 4096

_INHERITED_FLAG = "inherited_from_parent"


def _role_of(msg: Any) -> str | None:
    role = getattr(msg, "role", None)
    if isinstance(role, str):
        return role
    return None


def _content_of(msg: Any) -> list[Any]:
    content = getattr(msg, "content", None)
    if isinstance(content, list):
        return content
    return []


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit].rstrip() + "\n… (truncated)"


def _render_tool_input(value: Any) -> str:
    """Compact one-line-ish JSON for tool input args."""
    try:
        return json.dumps(value, indent=2, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return repr(value)


def _render_block(block: Any) -> str | None:
    """Render one content block; returns None if the block is structurally invisible."""
    block_type = getattr(block, "type", None)
    if block_type == "text":
        text = getattr(block, "text", "")
        if not text:
            return None
        return text
    if block_type == "thinking":
        # Parent thinking is rarely useful to the helper and may inflate the
        # transcript; surface only the marker so the helper knows the parent
        # paused to reason.
        return "_(thinking)_"
    if block_type == "tool_use":
        name = getattr(block, "name", "?")
        tool_input = getattr(block, "input", {})
        rendered = _render_tool_input(tool_input)
        return f"## tool_use: {name}\n\n```json\n{rendered}\n```"
    if block_type == "tool_result":
        content = getattr(block, "content", "")
        is_error = bool(getattr(block, "is_error", False))
        header = "## tool_result"
        if is_error:
            header += " [error]"
        body = _truncate(str(content), MAX_TOOL_RESULT_CHARS)
        return f"{header}\n\n{body}"
    if block_type == "system_notification":
        text = getattr(block, "text", "")
        if not text:
            return None
        return f"_(system notification: {text})_"
    return None


def _render_message(msg: Any) -> str | None:
    role = _role_of(msg) or "?"
    rendered_blocks: list[str] = []
    for block in _content_of(msg):
        piece = _render_block(block)
        if piece is not None:
            rendered_blocks.append(piece)
    if not rendered_blocks:
        return None
    body = "\n\n".join(rendered_blocks)
    return f"## role:{role}\n\n{body}"


def build_parent_transcript_block(messages: list[Any]) -> ContextBlock | None:
    """Build the ``parent_transcript`` block for a transcript-mode helper.

    Returns ``None`` when the transcript is empty after filtering, or when
    the first surviving message is not a user message (an unexpected shape
    we'd rather skip than render misleadingly).
    """
    # Stage 1: defensive system filter.
    filtered = [m for m in messages if _role_of(m) != "system"]
    if not filtered:
        return None

    # Stage 2: drop the first user message (helper's spawn prompt).
    if _role_of(filtered[0]) != "user":
        logger.warning(
            "build_parent_transcript_block: first non-system message has "
            "role=%r (expected 'user'); skipping transcript",
            _role_of(filtered[0]),
        )
        return None
    working = filtered[1:]
    if not working:
        return None

    tail = working[-MAX_TRANSCRIPT_MESSAGES:]
    rendered_messages = [r for r in (_render_message(m) for m in tail) if r]
    if not rendered_messages:
        return None

    text = "\n\n".join(rendered_messages)
    return ContextBlock(
        kind="parent_transcript",
        priority=ContextPriority.LOW,
        text=text,
        metadata={_INHERITED_FLAG: "true"},
    )


__all__ = [
    "MAX_TRANSCRIPT_MESSAGES",
    "MAX_TOOL_RESULT_CHARS",
    "build_parent_transcript_block",
]
