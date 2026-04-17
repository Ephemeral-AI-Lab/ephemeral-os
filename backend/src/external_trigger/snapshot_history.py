"""Formatting helpers for frozen conversation snapshots."""

from __future__ import annotations

import json
from typing import Any


def _stringify_content(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return json.dumps(value, indent=2, ensure_ascii=False, default=str)


def _format_content_block(block: Any) -> list[str]:
    if not isinstance(block, dict):
        text = _stringify_content(block)
        return ["```text", text, "```"] if text else []

    block_type = str(block.get("type") or "text")
    if block_type == "text":
        text = _stringify_content(block.get("text"))
        return ["```text", text, "```"] if text else []
    if block_type == "tool_use":
        name = str(block.get("name") or "unknown_tool")
        tool_input = block.get("input") if isinstance(block.get("input"), dict) else {}
        return [
            f"#### Tool call: {name}",
            "```json",
            json.dumps(tool_input, indent=2, ensure_ascii=False, default=str),
            "```",
        ]
    if block_type == "tool_result":
        status = "error" if block.get("is_error") else "ok"
        content = _stringify_content(block.get("content"))
        lines = [
            f"#### Tool result: {block.get('tool_use_id') or 'unknown_tool_use'} ({status})",
        ]
        if content:
            lines.extend(["```text", content, "```"])
        return lines

    return [
        f"#### Block: {block_type}",
        "```json",
        json.dumps(block, indent=2, ensure_ascii=False, default=str),
        "```",
    ]


def format_snapshot_history(messages: list[dict[str, Any]]) -> str:
    """Render frozen conversation history for an external-trigger prompt."""
    lines = ["## Snapshot History", ""]
    if not messages:
        lines.append("(none)")
        return "\n".join(lines)

    for index, message in enumerate(messages, start=1):
        role = str(message.get("role") or "unknown")
        lines.extend([f"### Message {index}: {role}", ""])
        content = message.get("content")
        if isinstance(content, list):
            block_lines: list[str] = []
            for block in content:
                block_lines.extend(_format_content_block(block))
                if block_lines and block_lines[-1] != "":
                    block_lines.append("")
            while block_lines and block_lines[-1] == "":
                block_lines.pop()
            lines.extend(block_lines or ["(empty)"])
        else:
            text = _stringify_content(content)
            if text:
                lines.extend(["```text", text, "```"])
            else:
                lines.append("(empty)")
        lines.append("")

    return "\n".join(lines).rstrip()
