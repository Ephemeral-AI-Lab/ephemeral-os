"""Soft reminder for resolver unresolved-count limit."""

from __future__ import annotations

import json
from typing import Any

from message.messages import ConversationMessage, ToolResultBlock, ToolUseBlock
from notification.rules import NotificationRule


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


def _unresolved_resolver_call_count(messages: list[Any]) -> int:
    tool_names_by_id: dict[str, str] = {}
    for message in messages:
        if not isinstance(message, ConversationMessage):
            continue
        for block in message.content:
            if isinstance(block, ToolUseBlock):
                tool_names_by_id[block.id] = block.name

    unresolved = 0
    for message in messages:
        if not isinstance(message, ConversationMessage):
            continue
        for block in message.content:
            if not isinstance(block, ToolResultBlock):
                continue
            if tool_names_by_id.get(block.tool_use_id) != "ask_resolver":
                continue
            if block.is_error or not _resolver_result_is_resolved(block):
                unresolved += 1
    return unresolved


def make_resolver_limit_reminder(*, warning_at: int = 4) -> NotificationRule:
    def _trigger(messages: list[Any], context: Any) -> bool:
        del context
        return _unresolved_resolver_call_count(messages) >= warning_at

    def _body(messages: list[Any], context: Any) -> str:
        del messages, context
        return (
            "One unresolved resolver call remains before success is blocked. "
            "Resolve and re-check the issues, or submit the failure terminal."
        )

    return NotificationRule(
        name="resolver_limit",
        trigger=_trigger,
        body=_body,
    )
