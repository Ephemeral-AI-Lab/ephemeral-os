"""Formatting helpers for frozen worker transcript evidence."""

from __future__ import annotations

import html
import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class _ConversationTurn:
    user_message: str = ""
    response_parts: list["_ResponsePart"] = field(default_factory=list)


@dataclass
class _ResponsePart:
    kind: str
    number: int = 0
    text: str = ""
    name: str = ""
    tool_use_id: str = ""
    tool_input: Any = field(default_factory=dict)
    tool_output: str = ""
    is_error: bool = False


def _stringify_content(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)


def _compact_json(value: Any) -> str:
    return json.dumps(
        value if value is not None else {},
        ensure_ascii=False,
        default=str,
        sort_keys=True,
        separators=(",", ":"),
    )


def _escape_attr(value: str) -> str:
    return html.escape(value, quote=True)


def _escape_text(value: str) -> str:
    return html.escape(value, quote=False)


def _tag(tag_name: str, **attrs: str) -> str:
    rendered_attrs = "".join(
        f' {key}="{_escape_attr(value)}"'
        for key, value in attrs.items()
        if value
    )
    return f"<{tag_name}{rendered_attrs}>"


def _append_escaped_block(lines: list[str], text: str, *, indent: str) -> None:
    if not text:
        lines.append(f"{indent}(none)")
        return
    for line in text.splitlines() or [""]:
        lines.append(f"{indent}{_escape_text(line)}")


def _tool_call_part(block: dict[str, Any]) -> _ResponsePart:
    name = str(block.get("name") or "unknown_tool")
    tool_input = block.get("input") if isinstance(block.get("input"), dict) else {}
    return _ResponsePart(
        kind="tool",
        name=name,
        tool_use_id=str(block.get("id") or ""),
        tool_input=tool_input,
    )


def _tool_result_part(block: dict[str, Any]) -> _ResponsePart:
    return _ResponsePart(
        kind="tool_result",
        tool_use_id=str(block.get("tool_use_id") or ""),
        tool_output=_stringify_content(block.get("content")),
        is_error=bool(block.get("is_error")),
    )


def _background_task_state_part(block: dict[str, Any]) -> _ResponsePart:
    status = str(block.get("status") or "unknown")
    tool_name = str(block.get("tool_name") or "unknown_tool")
    text = _stringify_content(block.get("text"))
    header = f"Background task state: {tool_name} ({status})"
    return _ResponsePart(kind="background_task_state", text=f"{header}\n{text}" if text else header)


def _response_part_from_block(block: Any, *, include_thinking: bool) -> _ResponsePart | None:
    if not isinstance(block, dict):
        text = _stringify_content(block)
        return _ResponsePart(kind="text", text=text) if text else None

    block_type = str(block.get("type") or "text")
    if block_type == "text":
        text = _stringify_content(block.get("text"))
        return _ResponsePart(kind="text", text=text) if text else None
    if block_type == "tool_use":
        return _tool_call_part(block)
    if block_type == "tool_result":
        return _tool_result_part(block)
    if block_type == "thinking":
        if not include_thinking:
            return None
        text = _stringify_content(block.get("text") or block.get("thinking"))
        return _ResponsePart(kind="thinking", text=text) if text else None
    if block_type == "system_reminder":
        text = _stringify_content(block.get("text"))
        return _ResponsePart(
            kind="system_reminder",
            text=f"System reminder:\n{text}" if text else "System reminder",
        )
    if block_type == "background_task_state":
        return _background_task_state_part(block)

    rendered = json.dumps(block, ensure_ascii=False, default=str, sort_keys=True)
    return _ResponsePart(kind="block", text=f"Block: {block_type}\n{rendered}")


def _split_user_content(content: Any, *, include_thinking: bool) -> tuple[str, list[_ResponsePart]]:
    if not isinstance(content, list):
        return _stringify_content(content), []

    user_parts: list[str] = []
    response_parts: list[_ResponsePart] = []
    for block in content:
        if isinstance(block, dict) and str(block.get("type") or "text") == "text":
            text = _stringify_content(block.get("text"))
            if text:
                user_parts.append(text)
            continue

        response_part = _response_part_from_block(block, include_thinking=include_thinking)
        if response_part is not None:
            response_parts.append(response_part)

    return "\n\n".join(user_parts), response_parts


def _format_response_content(content: Any, *, include_thinking: bool) -> list[_ResponsePart]:
    if not isinstance(content, list):
        text = _stringify_content(content)
        return [_ResponsePart(kind="text", text=text)] if text else []

    return [
        part
        for block in content
        if (part := _response_part_from_block(block, include_thinking=include_thinking)) is not None
    ]


def _turn_has_content(turn: _ConversationTurn) -> bool:
    return bool(
        turn.user_message.strip()
        or any(
            part.text.strip()
            or part.name.strip()
            or part.tool_use_id.strip()
            or part.tool_output.strip()
            for part in turn.response_parts
        )
    )


def _build_turns(messages: list[dict[str, Any]], *, include_thinking: bool) -> list[_ConversationTurn]:
    turns: list[_ConversationTurn] = []
    current = _ConversationTurn()

    for message in messages:
        role = str(message.get("role") or "unknown")
        content = message.get("content")
        if role == "user":
            user_message, response_parts = _split_user_content(content, include_thinking=include_thinking)
            current.response_parts.extend(response_parts)
            if user_message:
                if _turn_has_content(current):
                    turns.append(current)
                current = _ConversationTurn(user_message=user_message)
            continue

        current.response_parts.extend(_format_response_content(content, include_thinking=include_thinking))

    if _turn_has_content(current):
        turns.append(current)
    return turns


def _pair_tool_results(parts: list[_ResponsePart]) -> list[_ResponsePart]:
    pending_by_id: dict[str, _ResponsePart] = {}
    paired: list[_ResponsePart] = []
    tool_number = 0
    for part in parts:
        if part.kind == "tool":
            tool_number += 1
            part.number = tool_number
            paired.append(part)
            if part.tool_use_id:
                pending_by_id[part.tool_use_id] = part
            continue
        if part.kind == "tool_result":
            pending = pending_by_id.get(part.tool_use_id)
            if pending is not None:
                pending.tool_output = part.tool_output
                pending.is_error = part.is_error
            else:
                paired.append(part)
            continue
        paired.append(part)
    return paired


def _append_response_part(lines: list[str], part: _ResponsePart) -> None:
    if part.kind == "tool":
        lines.append(
            f"    {_tag('tool_call', number=str(part.number), name=part.name)}"
        )
        lines.append("      <input_json>")
        _append_escaped_block(lines, _compact_json(part.tool_input), indent="        ")
        lines.append("      </input_json>")
        status = "error" if part.is_error else "ok"
        lines.append(f"      {_tag('output', status=status)}")
        if part.tool_output:
            _append_escaped_block(lines, part.tool_output, indent="        ")
        else:
            lines.append("        (not available)")
        lines.append("      </output>")
        lines.append("    </tool_call>")
        return
    if part.kind == "tool_result":
        status = "error" if part.is_error else "ok"
        lines.append(f"    {_tag('tool_result', id=part.tool_use_id, status=status)}")
        _append_escaped_block(lines, part.tool_output, indent="      ")
        lines.append("    </tool_result>")
        return
    label = {
        "thinking": "thinking",
        "text": "text",
        "system_reminder": "system_reminder",
        "background_task_state": "background_task_state",
        "block": "block",
    }.get(part.kind, part.kind)
    lines.append(f"    <{label}>")
    _append_escaped_block(lines, part.text, indent="      ")
    lines.append(f"    </{label}>")


def format_snapshot_history(
    messages: list[dict[str, Any]],
    *,
    include_thinking: bool = False,
) -> str:
    """Render frozen worker conversation history as evidence."""
    lines = [
        "## Frozen Worker Transcript Evidence",
        "",
        '<worker_transcript evidence_only="true">',
    ]
    turns = _build_turns(messages, include_thinking=include_thinking)
    if not turns:
        lines.append("  (none)")
        lines.append("</worker_transcript>")
        return "\n".join(lines)

    for turn in turns:
        lines.append("  <worker_user_message>")
        _append_escaped_block(lines, turn.user_message.strip(), indent="    ")
        lines.append("  </worker_user_message>")
        lines.append("")
        lines.append("  <worker_assistant_activity>")
        response_parts = _pair_tool_results(turn.response_parts)
        if response_parts:
            for part in response_parts:
                _append_response_part(lines, part)
        else:
            lines.append("    (none)")
        lines.append("  </worker_assistant_activity>")

    lines.append("</worker_transcript>")

    return "\n".join(lines).rstrip()
