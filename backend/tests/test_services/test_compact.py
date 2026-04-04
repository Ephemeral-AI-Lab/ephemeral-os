"""Tests for compaction and token estimation helpers."""

from __future__ import annotations

from ephemeralos.engine.messages import ConversationMessage, TextBlock
from ephemeralos.services import estimate_message_tokens, estimate_tokens
from ephemeralos.services.compact import microcompact_messages


def test_token_estimation_helpers():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_message_tokens(["abcd", "abcdefgh"]) == 3


def test_microcompact_clears_old_tool_results():
    """Smoke test that microcompact runs without error on plain messages."""
    messages = [
        ConversationMessage(role="user", content=[TextBlock(text="first question")]),
        ConversationMessage(role="assistant", content=[TextBlock(text="first answer")]),
        ConversationMessage(role="user", content=[TextBlock(text="second question")]),
        ConversationMessage(role="assistant", content=[TextBlock(text="second answer")]),
    ]
    result, saved = microcompact_messages(messages)
    assert len(result) == 4
    assert saved == 0  # no tool results to clear
