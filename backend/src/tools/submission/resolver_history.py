"""Resolver-call transcript inspection helpers."""

from __future__ import annotations

import json
from typing import Any

from message.message import Message, ToolResultBlock, ToolUseBlock


def _resolver_result_is_resolved(block: ToolResultBlock) -> bool:
    metadata = block.metadata or {}
    resolver = metadata.get("resolver")
    if isinstance(resolver, dict) and resolver.get("resolved") is True:
        return True
    if metadata.get("resolved") is True:
        return True
    try:
        parsed = json.loads(block.content)
    except (TypeError, json.JSONDecodeError):
        return False
    if isinstance(parsed, dict):
        parsed_resolver = parsed.get("resolver")
        if isinstance(parsed_resolver, dict) and parsed_resolver.get("resolved") is True:
            return True
        return parsed.get("resolved") is True
    return False


def unresolved_resolver_call_count(messages: list[Any]) -> int:
    tool_names_by_id: dict[str, str] = {}
    for message in messages:
        if not isinstance(message, Message):
            continue
        for block in message.content:
            if isinstance(block, ToolUseBlock):
                tool_names_by_id[block.id] = block.name

    unresolved = 0
    for message in messages:
        if not isinstance(message, Message):
            continue
        for block in message.content:
            if not isinstance(block, ToolResultBlock):
                continue
            if tool_names_by_id.get(block.tool_use_id) != "ask_resolver":
                continue
            if block.is_error or not _resolver_result_is_resolved(block):
                unresolved += 1
    return unresolved


__all__ = ["unresolved_resolver_call_count"]
