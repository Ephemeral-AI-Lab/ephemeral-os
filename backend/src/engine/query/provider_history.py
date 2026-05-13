"""Provider-facing conversation history preparation."""

from __future__ import annotations

import copy

from engine.background.history import reduce_background_task_history
from message import (
    ConversationMessage,
    ToolResultBlock,
    ToolUseBlock,
)


def prepare_provider_messages(
    messages: list[ConversationMessage],
) -> list[ConversationMessage]:
    """Return a provider-safe copy of the durable message history.

    The query loop keeps ``messages`` as the append-only transcript.
    Providers receive a separate deep-copied view so stale background task
    snapshots and malformed historical tool pairs cannot leak into the next
    request. This function never mutates ``messages``.
    """
    return sanitize_tool_sequence(reduce_background_task_history(messages))


def sanitize_tool_sequence(
    messages: list[ConversationMessage],
) -> list[ConversationMessage]:
    """Drop malformed stale tool-use/result blocks from the provider view."""
    sanitized = copy.deepcopy(messages)
    _walk_tool_sequence(sanitized)
    return [msg for msg in sanitized if msg.content]


def _message_tool_use_ids(message: ConversationMessage) -> set[str]:
    return {block.id for block in message.content if isinstance(block, ToolUseBlock)}


def _message_tool_result_ids(message: ConversationMessage) -> set[str]:
    return {
        block.tool_use_id
        for block in message.content
        if isinstance(block, ToolResultBlock)
    }


def _walk_tool_sequence(messages: list[ConversationMessage]) -> None:
    pending_ids: set[str] = set()
    pending_msg_idx: int | None = None

    def _strip_tool_uses(idx: int | None, ids: set[str]) -> None:
        if idx is None or not ids:
            return
        message = messages[idx]
        message.content = [
            block
            for block in message.content
            if not (isinstance(block, ToolUseBlock) and block.id in ids)
        ]

    for msg_idx, message in enumerate(messages):
        tool_use_ids = _message_tool_use_ids(message)
        tool_result_ids = _message_tool_result_ids(message)
        satisfied_pending = False

        if pending_ids:
            if message.role != "user" or not pending_ids.issubset(tool_result_ids):
                _strip_tool_uses(pending_msg_idx, pending_ids)
                pending_ids = set()
                pending_msg_idx = None
                tool_result_ids = _message_tool_result_ids(message)
            else:
                extra = tool_result_ids - pending_ids
                if extra:
                    message.content = [
                        block
                        for block in message.content
                        if not (
                            isinstance(block, ToolResultBlock)
                            and block.tool_use_id in extra
                        )
                    ]
                pending_ids = set()
                pending_msg_idx = None
                tool_result_ids = _message_tool_result_ids(message)
                satisfied_pending = True

        if tool_result_ids and not tool_use_ids and not satisfied_pending:
            message.content = [
                block
                for block in message.content
                if not isinstance(block, ToolResultBlock)
            ]

        tool_use_ids = _message_tool_use_ids(message)
        if tool_use_ids:
            pending_ids = set(tool_use_ids)
            pending_msg_idx = msg_idx

    if pending_ids:
        _strip_tool_uses(pending_msg_idx, pending_ids)


__all__ = [
    "prepare_provider_messages",
    "sanitize_tool_sequence",
]
