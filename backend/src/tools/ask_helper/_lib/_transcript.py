"""Parent-transcript builder for helper tools.

Renders the parent agent's ``conversation_messages`` into a markdown
transcript string the helper can drop into ``user_msg_1`` under
``# Parent transcript``.

Two modes per plan §4.6:

* ``mode="advisor"`` — the advisor already sees the parent's first two
  user messages (verbatim ``user_msg_1`` and ``user_msg_2``) as separate
  sections in its own ``user_msg_1``, so the transcript starts at
  ``parent_messages[2:]``. Tool inputs are stripped for state-mutating
  tools (``Edit`` / ``Write`` / ``NotebookEdit``); ``Bash`` keeps only the
  command (capped at ``MAX_BASH_COMMAND_CHARS``); thinking blocks are
  dropped entirely.
* ``mode="resolver"`` — the resolver only needs the parent's spawn prompt
  dropped (drop the first user message), keeps tool inputs verbatim, and
  renders thinking blocks as ``_(thinking)_`` markers (legacy behaviour).

Both modes cap the message count at ``MAX_TRANSCRIPT_MESSAGES`` and the
total transcript bytes at ``MAX_TRANSCRIPT_BYTES`` with a head-trim
elision marker.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Literal

logger = logging.getLogger(__name__)

TranscriptMode = Literal["advisor", "resolver"]

MAX_TRANSCRIPT_MESSAGES = 40
MAX_TOOL_RESULT_CHARS = 4096
MAX_TRANSCRIPT_BYTES = 24576
MAX_BASH_COMMAND_CHARS = 500

# Tool names whose inputs are stripped in advisor mode (state-mutating).
# The advisor reviews payloads against the contract; full edit/write inputs
# inflate the transcript without changing the audit jurisdiction.
_ADVISOR_STRIP_INPUT_TOOLS = frozenset({"Edit", "Write", "NotebookEdit"})


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
    return text[:limit].rstrip() + "\n… (truncated)"


def _render_tool_input(value: Any) -> str:
    """Compact JSON for tool input args."""
    try:
        return json.dumps(value, indent=2, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return repr(value)


def _render_tool_use_advisor(name: str, tool_input: Any) -> str:
    """Advisor-mode tool_use rendering — strips state-mutating-tool inputs."""
    if name in _ADVISOR_STRIP_INPUT_TOOLS:
        return f"## tool_use: {name}\n\n(input elided)"
    if name == "Bash":
        command = ""
        if isinstance(tool_input, dict):
            command = str(tool_input.get("command", ""))
        command = _truncate(command, MAX_BASH_COMMAND_CHARS)
        return f"## tool_use: {name}\n\n```\n{command}\n```"
    rendered = _render_tool_input(tool_input)
    return f"## tool_use: {name}\n\n```json\n{rendered}\n```"


def _render_tool_use_resolver(name: str, tool_input: Any) -> str:
    """Resolver-mode tool_use rendering — preserves full inputs."""
    rendered = _render_tool_input(tool_input)
    return f"## tool_use: {name}\n\n```json\n{rendered}\n```"


def _render_block(block: Any, *, mode: TranscriptMode) -> str | None:
    block_type = getattr(block, "type", None)
    if block_type == "text":
        text = getattr(block, "text", "")
        if not text:
            return None
        return text
    if block_type == "thinking":
        if mode == "advisor":
            return None
        return "_(thinking)_"
    if block_type == "tool_use":
        name = getattr(block, "name", "?")
        tool_input = getattr(block, "input", {})
        if mode == "advisor":
            return _render_tool_use_advisor(name, tool_input)
        return _render_tool_use_resolver(name, tool_input)
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


def _render_message(msg: Any, *, mode: TranscriptMode) -> str | None:
    role = _role_of(msg) or "?"
    rendered_blocks: list[str] = []
    for block in _content_of(msg):
        piece = _render_block(block, mode=mode)
        if piece is not None:
            rendered_blocks.append(piece)
    if not rendered_blocks:
        return None
    body = "\n\n".join(rendered_blocks)
    return f"## role:{role}\n\n{body}"


def _apply_byte_cap(rendered_messages: list[str]) -> str:
    """Head-trim the rendered list to fit ``MAX_TRANSCRIPT_BYTES``.

    Drops oldest messages first; prepends ``(N earlier messages elided)`` if
    anything was trimmed.
    """
    text = "\n\n".join(rendered_messages)
    if len(text.encode("utf-8")) <= MAX_TRANSCRIPT_BYTES:
        return text
    kept = list(rendered_messages)
    elided = 0
    while kept:
        kept = kept[1:]
        elided += 1
        candidate = "\n\n".join(kept)
        prefix = f"(_{elided} earlier message{'s' if elided != 1 else ''} elided_)\n\n"
        if len((prefix + candidate).encode("utf-8")) <= MAX_TRANSCRIPT_BYTES:
            return prefix + candidate
    return f"(_{elided} earlier messages elided_)\n\n"


def build_parent_transcript(
    messages: list[Any], *, mode: TranscriptMode
) -> str | None:
    """Render parent ``conversation_messages`` as a transcript string.

    Returns ``None`` when the transcript is empty after filtering or the
    head-sequence is malformed (e.g. first non-system message is not a
    user message). The caller adds the ``# Parent transcript`` heading.
    """
    # Stage 1: defensive system filter.
    filtered = [m for m in messages if _role_of(m) != "system"]
    if not filtered:
        return None
    if _role_of(filtered[0]) != "user":
        logger.warning(
            "build_parent_transcript: first non-system message has "
            "role=%r (expected 'user'); skipping transcript",
            _role_of(filtered[0]),
        )
        return None

    # Stage 2: drop initial user messages. Advisor mode surfaces the
    # parent's user_msg_1 and user_msg_2 as separate sections of its own
    # user_msg_1, so we drop both here to avoid duplication. Resolver mode
    # only needs the spawn prompt dropped (legacy).
    drop_count = 2 if mode == "advisor" else 1
    working = filtered[drop_count:]
    if not working:
        return None

    tail = working[-MAX_TRANSCRIPT_MESSAGES:]
    rendered = [r for r in (_render_message(m, mode=mode) for m in tail) if r]
    if not rendered:
        return None

    return _apply_byte_cap(rendered)


__all__ = [
    "MAX_BASH_COMMAND_CHARS",
    "MAX_TOOL_RESULT_CHARS",
    "MAX_TRANSCRIPT_BYTES",
    "MAX_TRANSCRIPT_MESSAGES",
    "TranscriptMode",
    "build_parent_transcript",
]
