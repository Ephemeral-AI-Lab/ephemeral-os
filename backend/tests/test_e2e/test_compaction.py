# ruff: noqa
"""US-014: Text compaction system tests.

Unit-level tests for microcompact, full compact prompt generation,
auto-compact thresholds, and session state tracking.
These do NOT require live API keys.
"""

from __future__ import annotations

import pytest

from message import ConversationMessage, TextBlock, ToolResultBlock, ToolUseBlock
from compaction import (
    AUTOCOMPACT_BUFFER_TOKENS,
    COMPACTABLE_TOOLS,
    TIME_BASED_MC_CLEARED_MESSAGE,
    SessionState,
    build_compact_summary_message,
    estimate_message_tokens,
    format_compact_summary,
    get_autocompact_threshold,
    get_compact_prompt,
    microcompact_messages,
    should_autocompact,
)

pytestmark = [pytest.mark.e2e]


# ---------------------------------------------------------------------------
# Microcompact tests
# ---------------------------------------------------------------------------


class TestMicrocompact:
    """Test microcompact clears old tool results correctly."""

    def _make_tool_conversation(self, num_tool_calls: int) -> list[ConversationMessage]:
        """Build a conversation with alternating tool-use and tool-result messages."""
        messages: list[ConversationMessage] = []
        for i in range(num_tool_calls):
            tool_id = f"toolu_{i:04d}"
            # Assistant requests tool
            messages.append(
                ConversationMessage(
                    role="assistant",
                    content=[
                        TextBlock(text=f"Let me check file {i}."),
                        ToolUseBlock(id=tool_id, name="read_file", input={"path": f"/file{i}.txt"}),
                    ],
                )
            )
            # User provides tool result
            messages.append(
                ConversationMessage(
                    role="user",
                    content=[
                        ToolResultBlock(
                            tool_use_id=tool_id,
                            content=f"File content {i}: " + "x" * 500,
                            is_error=False,
                        ),
                    ],
                )
            )
        return messages

    def test_microcompact_clears_old_tool_results(self):
        """Microcompact should replace old tool result content with cleared message."""
        messages = self._make_tool_conversation(10)
        result, tokens_saved = microcompact_messages(messages, keep_recent=3)

        # Last 3 tool results should be preserved
        cleared_count = 0
        preserved_count = 0
        for msg in result:
            for block in msg.content:
                if isinstance(block, ToolResultBlock):
                    if block.content == TIME_BASED_MC_CLEARED_MESSAGE:
                        cleared_count += 1
                    else:
                        preserved_count += 1

        assert cleared_count == 7, f"Should clear 7 old results, cleared {cleared_count}"
        assert preserved_count == 3, (
            f"Should preserve 3 recent results, preserved {preserved_count}"
        )
        assert tokens_saved > 0, "Should have saved tokens"

    def test_microcompact_keeps_recent(self):
        """When fewer tool calls than keep_recent, nothing should be cleared."""
        messages = self._make_tool_conversation(3)
        result, tokens_saved = microcompact_messages(messages, keep_recent=5)

        assert tokens_saved == 0, "Should not clear anything when all are recent"
        for msg in result:
            for block in msg.content:
                if isinstance(block, ToolResultBlock):
                    assert block.content != TIME_BASED_MC_CLEARED_MESSAGE

    def test_microcompact_only_clears_compactable_tools(self):
        """Only tools in COMPACTABLE_TOOLS should be cleared."""
        messages = [
            # Compactable tool (read_file)
            ConversationMessage(
                role="assistant",
                content=[
                    ToolUseBlock(id="toolu_read", name="read_file", input={"path": "/a.txt"}),
                ],
            ),
            ConversationMessage(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="toolu_read", content="file content " * 100),
                ],
            ),
            # Non-compactable tool (custom_tool)
            ConversationMessage(
                role="assistant",
                content=[
                    ToolUseBlock(id="toolu_custom", name="custom_tool", input={}),
                ],
            ),
            ConversationMessage(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="toolu_custom", content="custom output " * 100),
                ],
            ),
            # Another compactable (keep_recent=1 will preserve this one)
            ConversationMessage(
                role="assistant",
                content=[
                    ToolUseBlock(id="toolu_bash", name="bash", input={"cmd": "ls"}),
                ],
            ),
            ConversationMessage(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="toolu_bash", content="bash output " * 100),
                ],
            ),
        ]

        result, _ = microcompact_messages(messages, keep_recent=1)

        # custom_tool result should NOT be cleared (not in COMPACTABLE_TOOLS)
        for msg in result:
            for block in msg.content:
                if isinstance(block, ToolResultBlock) and block.tool_use_id == "toolu_custom":
                    assert block.content != TIME_BASED_MC_CLEARED_MESSAGE, (
                        "Non-compactable tool should not be cleared"
                    )

    def test_compactable_tools_set(self):
        """Verify expected tools are in the compactable set."""
        assert "read_file" in COMPACTABLE_TOOLS
        assert "bash" in COMPACTABLE_TOOLS
        assert "grep" in COMPACTABLE_TOOLS
        assert "glob" in COMPACTABLE_TOOLS
        assert "edit_file" in COMPACTABLE_TOOLS
        assert "write_file" in COMPACTABLE_TOOLS


# ---------------------------------------------------------------------------
# Compact prompt generation
# ---------------------------------------------------------------------------


class TestCompactPrompt:
    """Test compact prompt includes all required sections."""

    def test_compact_prompt_generation(self):
        """Verify compact prompt includes critical instructions."""
        prompt = get_compact_prompt()
        assert "CRITICAL" in prompt
        assert "Do NOT call any tools" in prompt
        assert "<analysis>" in prompt
        assert "<summary>" in prompt
        assert "Primary Request" in prompt
        assert "Key Technical Concepts" in prompt
        assert "Files and Code" in prompt
        assert "Errors and Fixes" in prompt
        assert "Pending Tasks" in prompt

    def test_compact_prompt_with_custom_instructions(self):
        """Custom instructions should be appended."""
        prompt = get_compact_prompt("Focus on security aspects.")
        assert "Focus on security aspects." in prompt
        assert "<summary>" in prompt  # original sections still present

    def test_format_compact_summary_extracts_summary(self):
        """format_compact_summary should extract content from <summary> tags."""
        raw = "<analysis>some analysis</analysis>\n<summary>The actual summary</summary>"
        formatted = format_compact_summary(raw)
        assert "The actual summary" in formatted
        assert "some analysis" not in formatted

    def test_build_compact_summary_message(self):
        """build_compact_summary_message should create proper continuation text."""
        summary = "<summary>Test summary content</summary>"
        msg = build_compact_summary_message(summary, suppress_follow_up=True)
        assert "continued from a previous conversation" in msg
        assert "Test summary content" in msg
        assert "Continue the conversation" in msg

    def test_build_compact_summary_no_followup(self):
        """Without suppress_follow_up, continuation instruction should be absent."""
        summary = "<summary>Content here</summary>"
        msg = build_compact_summary_message(summary, suppress_follow_up=False)
        assert "continued from a previous conversation" in msg
        assert "Continue the conversation" not in msg


# ---------------------------------------------------------------------------
# Auto-compact threshold
# ---------------------------------------------------------------------------


class TestAutocompactThreshold:
    """Test threshold calculation for different models."""

    def test_autocompact_threshold_default(self):
        """Default model should have a reasonable threshold."""
        threshold = get_autocompact_threshold("claude-sonnet-4-20250514")
        assert threshold > 100_000, f"Threshold too low: {threshold}"
        assert threshold < 200_000, f"Threshold too high: {threshold}"

    def test_autocompact_threshold_includes_buffer(self):
        """Threshold should account for the buffer."""
        threshold = get_autocompact_threshold("any-model")
        # threshold = context_window - reserved - buffer
        # For 200k context: 200000 - 20000 - 13000 = 167000
        expected = 200_000 - 20_000 - AUTOCOMPACT_BUFFER_TOKENS
        assert threshold == expected

    def test_should_autocompact_below_threshold(self):
        """Should not compact when below threshold."""
        messages = [
            ConversationMessage.from_user_text("Hello"),
            ConversationMessage(role="assistant", content=[TextBlock(text="Hi there")]),
        ]
        state = SessionState()
        assert not should_autocompact(messages, "claude-sonnet-4-20250514", state)

    def test_should_autocompact_after_max_failures(self):
        """Should not compact after max consecutive failures."""
        # Create a large conversation that would trigger compaction
        messages = [ConversationMessage.from_user_text("x" * 100_000) for _ in range(50)]
        state = SessionState(consecutive_failures=3)
        assert not should_autocompact(messages, "claude-sonnet-4-20250514", state)


# ---------------------------------------------------------------------------
# Session state tracking
# ---------------------------------------------------------------------------


class TestSessionState:
    """Test SessionState tracks compaction state correctly."""

    def test_session_state_defaults(self):
        """Default state should have all fields at initial values."""
        state = SessionState()
        assert state.compacted is False
        assert state.turn_counter == 0
        assert state.consecutive_failures == 0

    def test_session_state_to_dict(self):
        """to_dict should serialize all fields."""
        state = SessionState(compacted=True, turn_counter=5, consecutive_failures=2)
        d = state.to_dict()
        assert d["compacted"] is True
        assert d["turn_counter"] == 5
        assert d["consecutive_failures"] == 2

    def test_session_state_from_dict(self):
        """from_dict should restore state from dict."""
        d = {"compacted": True, "turn_counter": 3, "consecutive_failures": 1}
        state = SessionState.from_dict(d)
        assert state.compacted is True
        assert state.turn_counter == 3
        assert state.consecutive_failures == 1

    def test_session_state_from_none(self):
        """from_dict(None) should return default state."""
        state = SessionState.from_dict(None)
        assert state.compacted is False
        assert state.turn_counter == 0

    def test_session_state_from_empty_dict(self):
        """from_dict({}) should return default state."""
        state = SessionState.from_dict({})
        assert state.compacted is False

    def test_session_state_roundtrip(self):
        """to_dict -> from_dict should be a lossless roundtrip."""
        original = SessionState(compacted=True, turn_counter=7, consecutive_failures=1)
        restored = SessionState.from_dict(original.to_dict())
        assert restored.compacted == original.compacted
        assert restored.turn_counter == original.turn_counter
        assert restored.consecutive_failures == original.consecutive_failures


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------


class TestTokenEstimation:
    """Test token estimation for messages."""

    def test_estimate_message_tokens_text(self):
        """Should estimate tokens for text messages."""
        messages = [
            ConversationMessage.from_user_text("Hello world"),
            ConversationMessage(role="assistant", content=[TextBlock(text="Hi there")]),
        ]
        tokens = estimate_message_tokens(messages)
        assert tokens > 0
        assert tokens < 100  # short messages

    def test_estimate_message_tokens_with_tools(self):
        """Messages with tool calls should have more tokens."""
        short_messages = [
            ConversationMessage.from_user_text("Hi"),
        ]
        tool_messages = [
            ConversationMessage.from_user_text("Hi"),
            ConversationMessage(
                role="assistant",
                content=[
                    ToolUseBlock(
                        id="t1", name="read_file", input={"path": "/very/long/path/to/file.txt"}
                    ),
                ],
            ),
            ConversationMessage(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="t1", content="x" * 1000),
                ],
            ),
        ]
        short_tokens = estimate_message_tokens(short_messages)
        tool_tokens = estimate_message_tokens(tool_messages)
        assert tool_tokens > short_tokens
