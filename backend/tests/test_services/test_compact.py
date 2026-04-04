"""Tests for compaction and token estimation helpers."""

from __future__ import annotations

from ephemeralos.engine.messages import ConversationMessage, TextBlock
from ephemeralos.services import (
    compact_messages,
    estimate_conversation_tokens,
    estimate_message_tokens,
    estimate_tokens,
    summarize_messages,
)


def test_token_estimation_helpers():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_message_tokens(["abcd", "abcdefgh"]) == 3


def test_compact_and_summarize_messages():
    messages = [
        ConversationMessage(role="user", content=[TextBlock(text="first question")]),
        ConversationMessage(role="assistant", content=[TextBlock(text="first answer")]),
        ConversationMessage(role="user", content=[TextBlock(text="second question")]),
        ConversationMessage(role="assistant", content=[TextBlock(text="second answer")]),
    ]

    summary = summarize_messages(messages, max_messages=2)
    assert "user: second question" in summary
    assert "assistant: second answer" in summary

    compacted = compact_messages(messages, preserve_recent=2)
    assert len(compacted) == 3
    assert "[conversation summary]" in compacted[0].text
    assert estimate_conversation_tokens(compacted) >= 1
