"""Tests for compaction and token estimation helpers."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from engine.messages import ConversationMessage, TextBlock, ToolResultBlock, ToolUseBlock
from models.types import ApiMessageCompleteEvent, UsageSnapshot
from utils import estimate_message_tokens, estimate_tokens
from utils.compact import (
    AUTOCOMPACT_BUFFER_TOKENS,
    COMPACTABLE_TOOLS,
    DEFAULT_KEEP_RECENT,
    MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES,
    MAX_OUTPUT_TOKENS_FOR_SUMMARY,
    TIME_BASED_MC_CLEARED_MESSAGE,
    SessionState,
    auto_compact_if_needed,
    build_compact_summary_message,
    compact_conversation,
    estimate_message_tokens as compact_estimate_tokens,
    format_compact_summary,
    get_autocompact_threshold,
    get_compact_prompt,
    microcompact_messages,
    should_autocompact,
)


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------


def test_token_estimation_helpers():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_message_tokens(["abcd", "abcdefgh"]) == 3


def test_compact_estimate_message_tokens_empty():
    assert compact_estimate_tokens([]) == 0


def test_compact_estimate_message_tokens_text_blocks():
    msgs = [
        ConversationMessage(role="user", content=[TextBlock(text="hello world")]),
        ConversationMessage(role="assistant", content=[TextBlock(text="hi there")]),
    ]
    result = compact_estimate_tokens(msgs)
    assert result > 0


def test_compact_estimate_message_tokens_tool_blocks():
    """Token estimation accounts for tool_use name+input and tool_result content."""
    msgs = [
        ConversationMessage(role="assistant", content=[
            ToolUseBlock(id="tu1", name="read_file", input={"path": "/tmp/foo.py"}),
        ]),
        ConversationMessage(role="user", content=[
            ToolResultBlock(tool_use_id="tu1", content="file contents here"),
        ]),
    ]
    result = compact_estimate_tokens(msgs)
    assert result > 0


# ---------------------------------------------------------------------------
# Microcompact
# ---------------------------------------------------------------------------


def _make_tool_exchange(tool_id: str, tool_name: str, result_content: str):
    """Create an assistant tool_use + user tool_result pair."""
    assistant = ConversationMessage(role="assistant", content=[
        ToolUseBlock(id=tool_id, name=tool_name, input={}),
    ])
    user = ConversationMessage(role="user", content=[
        ToolResultBlock(tool_use_id=tool_id, content=result_content),
    ])
    return assistant, user


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


def test_microcompact_clears_compactable_tool_results():
    """Old compactable tool results are replaced with cleared message."""
    messages = []
    # Create more tool exchanges than keep_recent (default 5)
    for i in range(8):
        a, u = _make_tool_exchange(f"tu{i}", "read_file", f"content of file {i}")
        messages.extend([a, u])

    result, saved = microcompact_messages(messages, keep_recent=3)
    assert saved > 0

    # The last 3 tool results should be preserved, the first 5 cleared
    cleared_count = 0
    preserved_count = 0
    for msg in result:
        for block in msg.content:
            if isinstance(block, ToolResultBlock):
                if block.content == TIME_BASED_MC_CLEARED_MESSAGE:
                    cleared_count += 1
                else:
                    preserved_count += 1
    assert cleared_count == 5
    assert preserved_count == 3


def test_microcompact_skips_noncompactable_tools():
    """Tools not in COMPACTABLE_TOOLS should never be cleared."""
    a, u = _make_tool_exchange("tu0", "custom_tool", "important result")
    # Add enough compactable ones to exceed keep_recent
    messages = [a, u]
    for i in range(1, 8):
        a2, u2 = _make_tool_exchange(f"tu{i}", "bash", f"output {i}")
        messages.extend([a2, u2])

    result, saved = microcompact_messages(messages, keep_recent=3)

    # The custom_tool result should remain intact
    custom_result = None
    for msg in result:
        for block in msg.content:
            if isinstance(block, ToolResultBlock) and block.tool_use_id == "tu0":
                custom_result = block
    assert custom_result is not None
    assert custom_result.content == "important result"


def test_microcompact_no_op_when_few_results():
    """When tool results <= keep_recent, nothing is cleared."""
    messages = []
    for i in range(3):
        a, u = _make_tool_exchange(f"tu{i}", "bash", f"output {i}")
        messages.extend([a, u])

    result, saved = microcompact_messages(messages, keep_recent=5)
    assert saved == 0


def test_microcompact_keep_recent_minimum_is_one():
    """keep_recent=0 is clamped to 1 — never clears ALL results."""
    messages = []
    for i in range(3):
        a, u = _make_tool_exchange(f"tu{i}", "grep", f"match {i}")
        messages.extend([a, u])

    result, saved = microcompact_messages(messages, keep_recent=0)
    # At least 1 result should be preserved
    preserved = sum(
        1 for msg in result for block in msg.content
        if isinstance(block, ToolResultBlock) and block.content != TIME_BASED_MC_CLEARED_MESSAGE
    )
    assert preserved >= 1


def test_microcompact_idempotent():
    """Running microcompact twice doesn't double-count savings."""
    messages = []
    for i in range(8):
        a, u = _make_tool_exchange(f"tu{i}", "read_file", f"content {i}" * 100)
        messages.extend([a, u])

    _, saved1 = microcompact_messages(messages, keep_recent=3)
    assert saved1 > 0

    # Second pass: already-cleared results should not contribute more savings
    _, saved2 = microcompact_messages(messages, keep_recent=3)
    assert saved2 == 0


def test_microcompact_preserves_is_error_flag():
    """Cleared tool results should keep their is_error flag."""
    a = ConversationMessage(role="assistant", content=[
        ToolUseBlock(id="tu_err", name="bash", input={}),
    ])
    u = ConversationMessage(role="user", content=[
        ToolResultBlock(tool_use_id="tu_err", content="command failed", is_error=True),
    ])
    # Add more to exceed keep_recent
    messages = [a, u]
    for i in range(6):
        a2, u2 = _make_tool_exchange(f"tu{i}", "bash", f"ok {i}")
        messages.extend([a2, u2])

    result, _ = microcompact_messages(messages, keep_recent=3)

    # Find the error result — it should be cleared but still marked as error
    for msg in result:
        for block in msg.content:
            if isinstance(block, ToolResultBlock) and block.tool_use_id == "tu_err":
                assert block.content == TIME_BASED_MC_CLEARED_MESSAGE
                assert block.is_error is True


# ---------------------------------------------------------------------------
# Format / build compact summary
# ---------------------------------------------------------------------------


def test_format_compact_summary_strips_analysis():
    raw = "<analysis>thinking stuff</analysis>\n<summary>The user asked about X.</summary>"
    result = format_compact_summary(raw)
    assert "thinking stuff" not in result
    assert "The user asked about X." in result


def test_format_compact_summary_no_tags():
    raw = "Just a plain summary without tags."
    result = format_compact_summary(raw)
    assert result == raw


def test_format_compact_summary_replaces_summary_tag():
    raw = "<summary>Key facts here.</summary>"
    result = format_compact_summary(raw)
    assert result.startswith("Summary:")
    assert "Key facts here." in result


def test_build_compact_summary_message_basic():
    msg = build_compact_summary_message("<summary>hello</summary>")
    assert "continued from a previous conversation" in msg
    assert "hello" in msg


def test_build_compact_summary_message_suppress_follow_up():
    msg = build_compact_summary_message("test", suppress_follow_up=True)
    assert "without asking" in msg


def test_build_compact_summary_message_recent_preserved():
    msg = build_compact_summary_message("test", recent_preserved=True)
    assert "Recent messages are preserved" in msg


def test_build_compact_summary_message_all_flags():
    msg = build_compact_summary_message("test", suppress_follow_up=True, recent_preserved=True)
    assert "Recent messages are preserved" in msg
    assert "without asking" in msg


# ---------------------------------------------------------------------------
# get_compact_prompt
# ---------------------------------------------------------------------------


def test_get_compact_prompt_default():
    prompt = get_compact_prompt()
    assert "CRITICAL" in prompt
    assert "summary" in prompt.lower()


def test_get_compact_prompt_custom_instructions():
    prompt = get_compact_prompt("Focus on database changes.")
    assert "Focus on database changes." in prompt


# ---------------------------------------------------------------------------
# SessionState
# ---------------------------------------------------------------------------


def test_session_state_defaults():
    s = SessionState()
    assert s.compacted is False
    assert s.turn_counter == 0
    assert s.consecutive_failures == 0


def test_session_state_roundtrip():
    s = SessionState(compacted=True, turn_counter=5, consecutive_failures=2)
    d = s.to_dict()
    s2 = SessionState.from_dict(d)
    assert s2.compacted is True
    assert s2.turn_counter == 5
    assert s2.consecutive_failures == 2


def test_session_state_from_dict_none():
    s = SessionState.from_dict(None)
    assert s.compacted is False


def test_session_state_from_dict_empty():
    s = SessionState.from_dict({})
    assert s.compacted is False
    assert s.turn_counter == 0


# ---------------------------------------------------------------------------
# Autocompact threshold / should_autocompact
# ---------------------------------------------------------------------------


def test_get_autocompact_threshold():
    threshold = get_autocompact_threshold("claude-sonnet-4-20250514")
    # context_window(200k) - reserved(20k) - buffer(13k) = 167k
    assert threshold == 200_000 - 20_000 - AUTOCOMPACT_BUFFER_TOKENS


def test_should_autocompact_under_threshold():
    msgs = [ConversationMessage.from_user_text("short")]
    state = SessionState()
    assert should_autocompact(msgs, "claude-sonnet-4-20250514", state) is False


def test_should_autocompact_over_threshold():
    # Create a message large enough to exceed the threshold
    big_text = "x" * (200_000 * 4)  # ~200k tokens
    msgs = [ConversationMessage.from_user_text(big_text)]
    state = SessionState()
    assert should_autocompact(msgs, "claude-sonnet-4-20250514", state) is True


def test_should_autocompact_respects_failure_limit():
    big_text = "x" * (200_000 * 4)
    msgs = [ConversationMessage.from_user_text(big_text)]
    state = SessionState(consecutive_failures=MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES)
    assert should_autocompact(msgs, "claude-sonnet-4-20250514", state) is False


# ---------------------------------------------------------------------------
# compact_conversation (async, with mock API client)
# ---------------------------------------------------------------------------


def _make_mock_api_client(summary_text: str) -> AsyncMock:
    """Create a mock API client that returns a canned summary."""
    client = AsyncMock()

    async def fake_stream(request):
        # Yield a single complete event with the summary
        complete_msg = ConversationMessage(role="assistant", content=[
            TextBlock(text=summary_text),
        ])
        yield ApiMessageCompleteEvent(
            message=complete_msg,
            usage=UsageSnapshot(),
        )

    client.stream_message = fake_stream
    return client


@pytest.mark.asyncio
async def test_compact_conversation_basic():
    """compact_conversation replaces old messages with a summary."""
    messages = []
    for i in range(10):
        messages.append(ConversationMessage.from_user_text(f"question {i}"))
        messages.append(ConversationMessage(role="assistant", content=[
            TextBlock(text=f"answer {i}"),
        ]))

    summary = "<analysis>analysis</analysis><summary>User asked 10 questions.</summary>"
    client = _make_mock_api_client(summary)

    result = await compact_conversation(
        messages,
        api_client=client,
        model="claude-sonnet-4-20250514",
        preserve_recent=4,
    )

    # Should have: 1 summary message + 4 preserved recent
    assert len(result) == 5
    assert "User asked 10 questions" in result[0].text
    assert "continued from a previous conversation" in result[0].text


@pytest.mark.asyncio
async def test_compact_conversation_too_few_messages():
    """If messages <= preserve_recent, returns them unchanged."""
    messages = [
        ConversationMessage.from_user_text("hi"),
        ConversationMessage(role="assistant", content=[TextBlock(text="hello")]),
    ]
    client = _make_mock_api_client("should not be called")

    result = await compact_conversation(
        messages,
        api_client=client,
        model="claude-sonnet-4-20250514",
        preserve_recent=6,
    )
    assert len(result) == 2


@pytest.mark.asyncio
async def test_compact_conversation_empty_summary_returns_original():
    """If the LLM returns empty text, original messages are returned."""
    messages = []
    for i in range(10):
        messages.append(ConversationMessage.from_user_text(f"q{i}"))
        messages.append(ConversationMessage(role="assistant", content=[TextBlock(text=f"a{i}")]))

    client = _make_mock_api_client("")

    result = await compact_conversation(
        messages,
        api_client=client,
        model="claude-sonnet-4-20250514",
        preserve_recent=4,
    )
    assert len(result) == 20  # unchanged


@pytest.mark.asyncio
async def test_compact_conversation_custom_instructions():
    """Custom instructions are included in the compact prompt."""
    messages = []
    for i in range(10):
        messages.append(ConversationMessage.from_user_text(f"q{i}"))
        messages.append(ConversationMessage(role="assistant", content=[TextBlock(text=f"a{i}")]))

    captured_requests = []
    client = AsyncMock()

    async def capture_stream(request):
        captured_requests.append(request)
        msg = ConversationMessage(role="assistant", content=[
            TextBlock(text="<summary>done</summary>"),
        ])
        yield ApiMessageCompleteEvent(message=msg, usage=UsageSnapshot())

    client.stream_message = capture_stream

    await compact_conversation(
        messages,
        api_client=client,
        model="claude-sonnet-4-20250514",
        custom_instructions="Focus on SQL queries.",
    )

    assert len(captured_requests) == 1
    # The last message in the compact request should contain our custom instructions
    last_msg = captured_requests[0].messages[-1]
    assert "Focus on SQL queries." in last_msg.text


# ---------------------------------------------------------------------------
# auto_compact_if_needed (async)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_compact_not_needed():
    """When under threshold, messages are returned unchanged."""
    msgs = [ConversationMessage.from_user_text("short message")]
    state = SessionState()
    client = _make_mock_api_client("unused")

    result, was_compacted = await auto_compact_if_needed(
        msgs,
        api_client=client,
        model="claude-sonnet-4-20250514",
        state=state,
    )
    assert was_compacted is False
    assert len(result) == 1


@pytest.mark.asyncio
async def test_auto_compact_triggered_full():
    """When over threshold, full compaction fires and updates state."""
    # Build messages large enough to exceed threshold
    big_text = "x" * (200_000 * 4)
    msgs = [
        ConversationMessage.from_user_text(big_text),
        *[ConversationMessage.from_user_text(f"q{i}") for i in range(10)],
        *[ConversationMessage(role="assistant", content=[TextBlock(text=f"a{i}")]) for i in range(10)],
    ]
    state = SessionState()
    client = _make_mock_api_client("<summary>Compacted.</summary>")

    result, was_compacted = await auto_compact_if_needed(
        msgs,
        api_client=client,
        model="claude-sonnet-4-20250514",
        state=state,
    )
    assert was_compacted is True
    assert state.compacted is True
    assert state.consecutive_failures == 0
    assert state.turn_counter == 1
    # Result should be shorter than original
    assert len(result) < len(msgs)


@pytest.mark.asyncio
async def test_auto_compact_failure_increments_counter():
    """If compact_conversation raises, consecutive_failures is incremented."""
    big_text = "x" * (200_000 * 4)
    msgs = [
        ConversationMessage.from_user_text(big_text),
        *[ConversationMessage.from_user_text(f"q{i}") for i in range(10)],
        *[ConversationMessage(role="assistant", content=[TextBlock(text=f"a{i}")]) for i in range(10)],
    ]
    state = SessionState()

    client = AsyncMock()

    async def exploding_stream(request):
        raise RuntimeError("API error")
        yield  # make it a generator  # noqa: E501

    client.stream_message = exploding_stream

    result, was_compacted = await auto_compact_if_needed(
        msgs,
        api_client=client,
        model="claude-sonnet-4-20250514",
        state=state,
    )
    assert was_compacted is False
    assert state.consecutive_failures == 1


@pytest.mark.asyncio
async def test_auto_compact_stops_after_max_failures():
    """After MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES, autocompact is skipped."""
    big_text = "x" * (200_000 * 4)
    msgs = [ConversationMessage.from_user_text(big_text)]
    state = SessionState(consecutive_failures=MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES)

    client = _make_mock_api_client("should not be called")

    result, was_compacted = await auto_compact_if_needed(
        msgs,
        api_client=client,
        model="claude-sonnet-4-20250514",
        state=state,
    )
    assert was_compacted is False


# ---------------------------------------------------------------------------
# COMPACTABLE_TOOLS constant
# ---------------------------------------------------------------------------


def test_compactable_tools_contains_expected():
    expected = {"read_file", "bash", "grep", "glob", "web_search", "web_fetch", "edit_file", "write_file"}
    assert COMPACTABLE_TOOLS == expected
