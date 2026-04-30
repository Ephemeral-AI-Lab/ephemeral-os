"""Prehook blocking success terminals after unresolved resolver calls."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from message.messages import ConversationMessage, ToolResultBlock, ToolUseBlock
from pydantic import BaseModel

from tools.core.context import ToolExecutionContextService
from tools.core.hooks import HookResult


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


@dataclass(frozen=True, slots=True)
class ResolverSuccessLimitGate:
    target_tool: str
    limit: int = 5

    async def run(
        self,
        tool_input: BaseModel,
        context: ToolExecutionContextService,
    ) -> HookResult[Any]:
        messages = context.get("conversation_messages", [])
        count = unresolved_resolver_call_count(messages if isinstance(messages, list) else [])
        if count >= self.limit:
            return HookResult.fail(
                "Success is blocked after five unresolved resolver calls. Submit "
                "the corresponding failure terminal with the remaining issues."
            )
        return HookResult.pass_(tool_input)
