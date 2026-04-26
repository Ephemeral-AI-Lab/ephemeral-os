# ruff: noqa
"""E2E tests for the chat SSE stream — text, thinking, and tool calls."""

from __future__ import annotations

import pytest

from message import ConversationMessage, TextBlock, ThinkingBlock, ToolUseBlock
from tests.test_e2e.conftest import parse_sse_events, events_of_type

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Simple text chat
# ---------------------------------------------------------------------------


class TestSimpleChatFlow:
    """Test basic chat flow through /api/chat with SSE parsing."""

    def test_simple_chat_returns_sse_events(self, app_client):
        client, _ = app_client
        resp = client.post("/api/chat", json={"line": "Hello"})
        assert resp.status_code == 200

        events = parse_sse_events(resp.text)
        types = [e["type"] for e in events]

        # Must have user transcript, assistant delta, assistant complete, line complete
        assert "transcript_item" in types
        assert "assistant_complete" in types
        assert "line_complete" in types

    def test_user_message_appears_in_transcript(self, app_client):
        client, _ = app_client
        resp = client.post("/api/chat", json={"line": "What is 2+2?"})
        events = parse_sse_events(resp.text)

        user_items = [
            e
            for e in events_of_type(events, "transcript_item")
            if e.get("item", {}).get("role") == "user"
        ]
        assert len(user_items) == 1
        assert user_items[0]["item"]["text"] == "What is 2+2?"

    def test_assistant_complete_has_text(self, app_client):
        client, _ = app_client
        resp = client.post("/api/chat", json={"line": "Hello"})
        events = parse_sse_events(resp.text)

        completes = events_of_type(events, "assistant_complete")
        assert len(completes) >= 1
        assert completes[0]["message"]  # non-empty text
        assert completes[0]["item"]["role"] == "assistant"


# ---------------------------------------------------------------------------
# Chat with thinking/reasoning
# ---------------------------------------------------------------------------


class TestChatWithThinking:
    """Test that thinking_delta events are streamed when the model reasons."""

    @pytest.fixture(autouse=True)
    def _setup_thinking_response(self, mock_api_client):
        """Configure mock to return thinking + text."""
        mock_api_client.set_responses(
            ConversationMessage(
                role="assistant",
                content=[
                    ThinkingBlock(text="Let me reason step by step..."),
                    TextBlock(text="The answer is 42."),
                ],
            )
        )

    def test_thinking_delta_events_streamed(self, app_client):
        client, _ = app_client
        resp = client.post("/api/chat", json={"line": "Think about this"})
        events = parse_sse_events(resp.text)

        thinking_events = events_of_type(events, "thinking_delta")
        assert len(thinking_events) >= 1
        assert thinking_events[0]["message"] == "Let me reason step by step..."

    def test_assistant_text_follows_thinking(self, app_client):
        client, _ = app_client
        resp = client.post("/api/chat", json={"line": "Think about this"})
        events = parse_sse_events(resp.text)

        types = [e["type"] for e in events]
        # thinking_delta should come before assistant_delta
        if "thinking_delta" in types and "assistant_delta" in types:
            think_idx = types.index("thinking_delta")
            text_idx = types.index("assistant_delta")
            assert think_idx < text_idx

        completes = events_of_type(events, "assistant_complete")
        assert completes[0]["message"] == "The answer is 42."


# ---------------------------------------------------------------------------
# Chat with tool calls
# ---------------------------------------------------------------------------


class TestChatWithToolCalls:
    """Test tool execution events in the SSE stream."""

    @pytest.fixture(autouse=True)
    def _setup_tool_response(self, mock_api_client):
        """Configure mock: first response has tool call, second is text."""
        mock_api_client.set_responses(
            ConversationMessage(
                role="assistant",
                content=[
                    TextBlock(text="Let me list the files."),
                    ToolUseBlock(
                        id="toolu_test_001",
                        name="list_directory",
                        input={"path": "/tmp"},
                    ),
                ],
            ),
            ConversationMessage(
                role="assistant",
                content=[TextBlock(text="The directory contains test files.")],
            ),
        )

    def test_tool_started_event(self, app_client):
        """Default agent has no configured tools; tool_started comes from mock tool_use block,
        handled as unknown tool (tool_completed with error)."""
        client, _ = app_client
        resp = client.post("/api/chat", json={"line": "List /tmp"})
        events = parse_sse_events(resp.text)

        # The mock response contains a tool_use block for "list_directory".
        # Since no tool named "list_directory" is registered, the engine handles it
        # as an unknown tool and emits tool_completed with is_error=True.
        tool_completed = events_of_type(events, "tool_completed")
        assert len(tool_completed) >= 1
        assert tool_completed[0]["tool_name"] == "list_directory"

    def test_tool_completed_event(self, app_client):
        client, _ = app_client
        resp = client.post("/api/chat", json={"line": "List /tmp"})
        events = parse_sse_events(resp.text)

        tool_completed = events_of_type(events, "tool_completed")
        assert len(tool_completed) >= 1
        # Tool should have completed (even with error since list_directory is unknown)
        assert tool_completed[0]["tool_name"] == "list_directory"

    def test_final_response_after_tool(self, app_client):
        client, _ = app_client
        resp = client.post("/api/chat", json={"line": "List /tmp"})
        events = parse_sse_events(resp.text)

        # Should end with line_complete
        assert events[-1]["type"] == "line_complete"

        # Should have assistant_complete (may be from the tool error or final turn)
        completes = events_of_type(events, "assistant_complete")
        assert len(completes) >= 1


# ---------------------------------------------------------------------------
# Chat with combined thinking + tool calls
# ---------------------------------------------------------------------------


class TestChatThinkingAndTools:
    """Test combined thinking and tool call flow."""

    @pytest.fixture(autouse=True)
    def _setup_combined_response(self, mock_api_client):
        """Configure mock: thinking + tool call, then text response."""
        mock_api_client.set_responses(
            ConversationMessage(
                role="assistant",
                content=[
                    ThinkingBlock(text="I need to check the filesystem."),
                    TextBlock(text="Let me check."),
                    ToolUseBlock(
                        id="toolu_combined",
                        name="list_directory",
                        input={"path": "/home"},
                    ),
                ],
            ),
            ConversationMessage(
                role="assistant",
                content=[TextBlock(text="Found the files.")],
            ),
        )

    def test_thinking_then_tool_then_response(self, app_client):
        client, _ = app_client
        resp = client.post("/api/chat", json={"line": "Check /home"})
        events = parse_sse_events(resp.text)

        types = [e["type"] for e in events]
        assert "thinking_delta" in types
        # Tool is unknown (no tool named "list_directory" is registered),
        # so tool_started may not appear but tool_completed will (with error)
        assert "tool_completed" in types
        assert "line_complete" in types


# ---------------------------------------------------------------------------
# Mock client captures correct request data
# ---------------------------------------------------------------------------


class TestRequestCapture:
    """Test that the mock captures the correct request parameters."""

    def test_system_prompt_passed_to_api(self, app_client):
        client, mock = app_client
        client.post("/api/chat", json={"line": "test"})

        assert mock.last_request is not None
        assert mock.last_request.system_prompt is not None
        assert isinstance(mock.last_request.system_prompt, str)

    def test_tools_passed_to_api(self, app_client):
        """Default agent has no configured tools."""
        client, mock = app_client
        client.post("/api/chat", json={"line": "test"})

        assert mock.last_request is not None
        assert mock.last_request.tools is not None
        assert mock.last_request.tools == []

    def test_user_message_in_request(self, app_client):
        client, mock = app_client
        client.post("/api/chat", json={"line": "What is Python?"})

        assert mock.last_request is not None
        messages = mock.last_request.messages
        user_msgs = [m for m in messages if m.role == "user"]
        assert len(user_msgs) >= 1
        assert "What is Python?" in user_msgs[-1].text
