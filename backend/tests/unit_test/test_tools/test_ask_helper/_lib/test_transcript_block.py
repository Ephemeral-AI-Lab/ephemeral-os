"""Tests for ``tools.ask_helper._lib._transcript.build_parent_transcript_block``.

Locks the two-stage filter (drop ``role=='system'`` defensively, drop the
FIRST user message which is the helper's spawn prompt), the message-count
cap, the tool-result truncation, and the returned block's
priority/metadata.
"""

from __future__ import annotations

import logging

from message.messages import (
    ConversationMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from task_center.context_engine.packet import ContextPriority
from tools.ask_helper._lib._transcript import (
    MAX_TOOL_RESULT_CHARS,
    MAX_TRANSCRIPT_MESSAGES,
    build_parent_transcript_block,
)


def _user(text: str) -> ConversationMessage:
    return ConversationMessage(role="user", content=[TextBlock(text=text)])


def _assistant(text: str) -> ConversationMessage:
    return ConversationMessage(role="assistant", content=[TextBlock(text=text)])


def _assistant_tool(name: str, **kwargs) -> ConversationMessage:
    return ConversationMessage(
        role="assistant", content=[ToolUseBlock(name=name, input=kwargs)]
    )


def _user_tool_result(content: str, *, is_error: bool = False) -> ConversationMessage:
    return ConversationMessage(
        role="user",
        content=[
            ToolResultBlock(tool_use_id="t1", content=content, is_error=is_error)
        ],
    )


def test_empty_messages_returns_none():
    assert build_parent_transcript_block([]) is None


def test_only_first_user_message_returns_none():
    # After dropping the spawn prompt nothing is left.
    assert build_parent_transcript_block([_user("spawn prompt")]) is None


def test_first_non_user_message_returns_none_and_logs_warning(caplog):
    caplog.set_level(logging.WARNING)
    block = build_parent_transcript_block([_assistant("hi")])
    assert block is None
    assert any(
        "first non-system message has role" in rec.message
        for rec in caplog.records
    )


def test_system_messages_filtered_defensively():
    # Build a sequence where a "system" role sneaks in. The defensive
    # filter drops it; the FIRST surviving message must still be 'user'
    # (the spawn prompt), which is then itself dropped by Stage 2.
    class _Sys:
        role = "system"
        content = [TextBlock(text="ignored")]

    msgs = [
        _Sys(),
        _user("spawn prompt"),  # stage-2 drops this
        _assistant("kept after filter"),
    ]
    block = build_parent_transcript_block(msgs)
    assert block is not None
    assert "ignored" not in block.text
    assert "spawn prompt" not in block.text
    assert "kept after filter" in block.text


def test_first_user_message_dropped_subsequent_user_tool_results_kept():
    msgs = [
        _user("spawn prompt"),
        _assistant_tool("shell", cmd="pytest"),
        _user_tool_result("2 failed", is_error=True),
    ]
    block = build_parent_transcript_block(msgs)
    assert block is not None
    assert "spawn prompt" not in block.text
    assert "tool_use: shell" in block.text
    assert "tool_result" in block.text
    assert "2 failed" in block.text


def test_returned_block_priority_kind_and_inherited_metadata():
    msgs = [_user("spawn"), _assistant("hello")]
    block = build_parent_transcript_block(msgs)
    assert block is not None
    assert block.kind == "parent_transcript"
    assert block.priority == ContextPriority.LOW
    assert block.metadata.get("inherited_from_parent") == "true"


def test_message_count_capped_at_max_transcript_messages():
    msgs = [_user("spawn")] + [_assistant(f"msg {i}") for i in range(200)]
    block = build_parent_transcript_block(msgs)
    assert block is not None
    # The cap takes the LAST MAX_TRANSCRIPT_MESSAGES of the post-filter
    # working list. With 200 assistant messages, only the trailing
    # MAX_TRANSCRIPT_MESSAGES of them appear.
    boundary = 200 - MAX_TRANSCRIPT_MESSAGES
    assert f"msg {boundary}" in block.text  # first kept
    assert f"msg {boundary - 1}" not in block.text  # last dropped
    assert "msg 199" in block.text


def test_tool_result_content_truncated_at_max_chars():
    huge = "X" * (MAX_TOOL_RESULT_CHARS + 5_000)
    msgs = [
        _user("spawn"),
        _assistant_tool("shell", cmd="something"),
        _user_tool_result(huge),
    ]
    block = build_parent_transcript_block(msgs)
    assert block is not None
    # The full 9k payload is not present; the truncated marker is.
    assert huge not in block.text
    assert "truncated" in block.text
    # Some prefix of the huge payload IS present.
    assert "X" * 64 in block.text


def test_tool_result_error_flag_renders_marker():
    msgs = [
        _user("spawn"),
        _assistant_tool("shell"),
        _user_tool_result("boom", is_error=True),
    ]
    block = build_parent_transcript_block(msgs)
    assert block is not None
    assert "tool_result [error]" in block.text
