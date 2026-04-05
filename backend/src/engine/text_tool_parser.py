"""Parse [TOOL_CALL]...[/TOOL_CALL] markers from model text.

Models like MiniMax embed tool calls as text markers instead of using
the structured function-calling API. This module extracts those markers
into ToolUseBlock instances.
"""

from __future__ import annotations

import json
import re
import uuid

from engine.messages import ToolUseBlock

_TEXT_TOOL_CALL_RE = re.compile(
    r"\[TOOL_CALL\]\s*(.*?)\s*\[/TOOL_CALL\]", re.DOTALL
)


def parse_text_tool_calls(text: str) -> list[ToolUseBlock]:
    """Parse [TOOL_CALL]...[/TOOL_CALL] markers from model text.

    Supports formats like:
      {tool => "name", args => {...}}
      {"tool": "name", "args": {...}}
    """
    results: list[ToolUseBlock] = []
    for match in _TEXT_TOOL_CALL_RE.finditer(text):
        raw = match.group(1).strip()
        tool_name: str | None = None
        tool_args: dict = {}

        # Try JSON format first: {"tool": "name", "args": {...}}
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                tool_name = parsed.get("tool") or parsed.get("name")
                tool_args = parsed.get("args") or parsed.get("input") or {}
        except (json.JSONDecodeError, TypeError):
            pass

        # Fallback: {tool => "name", args => {...}}
        if tool_name is None:
            name_match = re.search(r'tool\s*(?:=>|:)\s*"([^"]+)"', raw)
            if name_match:
                tool_name = name_match.group(1)
            args_match = re.search(r'args\s*(?:=>|:)\s*(\{[\s\S]*\})', raw)
            if args_match:
                try:
                    tool_args = json.loads(args_match.group(1))
                except (json.JSONDecodeError, TypeError):
                    tool_args = {}

        if tool_name:
            results.append(ToolUseBlock(
                id=f"text-tc-{uuid.uuid4().hex[:8]}",
                name=tool_name,
                input=tool_args,
            ))
    return results
