"""Tests for ThinkingBlock support in the message pipeline."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from message import (
    ConversationMessage,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
    ToolResultBlock,
    serialize_content_block,
    assistant_message_from_api,
)
from models.types import (
    ApiMessageCompleteEvent,
    ApiTextDeltaEvent,
    ApiThinkingDeltaEvent,
    UsageSnapshot,
)
from message.stream_events import (
    AssistantTextDelta,
    AssistantTurnComplete,
    ThinkingDelta,
)
from models.clients.openai_compat import (
    _convert_assistant_message,
    _convert_messages_to_openai,
)


# ---------------------------------------------------------------------------
# ThinkingBlock model tests
# ---------------------------------------------------------------------------


class TestThinkingBlock:
    """Test the ThinkingBlock content type."""

    def test_create(self):
        block = ThinkingBlock(text="Let me reason about this...")
        assert block.type == "thinking"
        assert block.text == "Let me reason about this..."

    def test_default_type(self):
        block = ThinkingBlock(text="reasoning")
        assert block.type == "thinking"

    def test_serialize(self):
        block = ThinkingBlock(text="step 1: analyze")
        data = block.model_dump(mode="json")
        assert data == {"type": "thinking", "text": "step 1: analyze"}

    def test_deserialize(self):
        data = {"type": "thinking", "text": "some reasoning"}
        block = ThinkingBlock.model_validate(data)
        assert block.type == "thinking"
        assert block.text == "some reasoning"


# ---------------------------------------------------------------------------
# ConversationMessage with ThinkingBlock
# ---------------------------------------------------------------------------


class TestConversationMessageThinking:
    """Test ConversationMessage handling of ThinkingBlock content."""

    def test_thinking_property(self):
        msg = ConversationMessage(
            role="assistant",
            content=[
                ThinkingBlock(text="First I'll "),
                ThinkingBlock(text="reason about this."),
                TextBlock(text="Here's my answer."),
            ],
        )
        assert msg.thinking == "First I'll reason about this."
        assert msg.text == "Here's my answer."

    def test_thinking_property_empty(self):
        msg = ConversationMessage(
            role="assistant",
            content=[TextBlock(text="No thinking here.")],
        )
        assert msg.thinking == ""

    def test_tool_uses_excludes_thinking(self):
        msg = ConversationMessage(
            role="assistant",
            content=[
                ThinkingBlock(text="reasoning"),
                ToolUseBlock(id="t1", name="read_file", input={"path": "/tmp/x"}),
            ],
        )
        assert len(msg.tool_uses) == 1
        assert msg.tool_uses[0].name == "read_file"

    def test_to_api_param_strips_thinking(self):
        """Anthropic API does not accept thinking blocks back in messages."""
        msg = ConversationMessage(
            role="assistant",
            content=[
                ThinkingBlock(text="internal reasoning"),
                TextBlock(text="visible answer"),
            ],
        )
        param = msg.to_api_param()
        assert param["role"] == "assistant"
        assert len(param["content"]) == 1
        assert param["content"][0] == {"type": "text", "text": "visible answer"}

    def test_to_api_param_strips_thinking_with_tool_calls(self):
        msg = ConversationMessage(
            role="assistant",
            content=[
                ThinkingBlock(text="Let me think..."),
                TextBlock(text="I'll read the file."),
                ToolUseBlock(id="t1", name="read_file", input={"path": "/a"}),
            ],
        )
        param = msg.to_api_param()
        assert len(param["content"]) == 2
        assert param["content"][0]["type"] == "text"
        assert param["content"][1]["type"] == "tool_use"


# ---------------------------------------------------------------------------
# Serialization round-trip (DB persistence)
# ---------------------------------------------------------------------------


class TestThinkingBlockPersistence:
    """Test that ThinkingBlock survives model_dump / model_validate round-trips."""

    def test_round_trip_single_thinking(self):
        msg = ConversationMessage(
            role="assistant",
            content=[
                ThinkingBlock(text="reasoning"),
                TextBlock(text="answer"),
            ],
        )
        dumped = msg.model_dump(mode="json")
        restored = ConversationMessage.model_validate(dumped)
        assert len(restored.content) == 2
        assert isinstance(restored.content[0], ThinkingBlock)
        assert restored.content[0].text == "reasoning"
        assert isinstance(restored.content[1], TextBlock)
        assert restored.content[1].text == "answer"

    def test_round_trip_thinking_with_tools(self):
        msg = ConversationMessage(
            role="assistant",
            content=[
                ThinkingBlock(text="I should read the file"),
                TextBlock(text="Let me check."),
                ToolUseBlock(id="t1", name="read_file", input={"path": "/tmp/x"}),
            ],
        )
        dumped = msg.model_dump(mode="json")
        restored = ConversationMessage.model_validate(dumped)
        assert len(restored.content) == 3
        assert restored.thinking == "I should read the file"
        assert restored.text == "Let me check."
        assert len(restored.tool_uses) == 1

    def test_round_trip_no_thinking(self):
        """Messages without thinking still round-trip correctly."""
        msg = ConversationMessage(
            role="assistant",
            content=[TextBlock(text="plain answer")],
        )
        dumped = msg.model_dump(mode="json")
        restored = ConversationMessage.model_validate(dumped)
        assert len(restored.content) == 1
        assert restored.thinking == ""

    def test_json_serializable(self):
        """Ensure the dumped form is JSON-serializable (for DB storage)."""
        msg = ConversationMessage(
            role="assistant",
            content=[
                ThinkingBlock(text="step 1"),
                TextBlock(text="done"),
            ],
        )
        dumped = msg.model_dump(mode="json")
        json_str = json.dumps(dumped)
        loaded = json.loads(json_str)
        restored = ConversationMessage.model_validate(loaded)
        assert restored.thinking == "step 1"


# ---------------------------------------------------------------------------
# serialize_content_block
# ---------------------------------------------------------------------------


class TestSerializeContentBlock:
    """Test serialize_content_block with ThinkingBlock."""

    def test_serialize_thinking(self):
        block = ThinkingBlock(text="reasoning content")
        result = serialize_content_block(block)
        assert result == {"type": "thinking", "text": "reasoning content"}

    def test_serialize_text(self):
        block = TextBlock(text="hello")
        result = serialize_content_block(block)
        assert result == {"type": "text", "text": "hello"}

    def test_serialize_tool_use(self):
        block = ToolUseBlock(id="t1", name="bash", input={"cmd": "ls"})
        result = serialize_content_block(block)
        assert result["type"] == "tool_use"
        assert result["name"] == "bash"


# ---------------------------------------------------------------------------
# assistant_message_from_api (Anthropic SDK response parsing)
# ---------------------------------------------------------------------------


class _FakeBlock:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class TestAssistantMessageFromApiThinking:
    """Test that assistant_message_from_api parses thinking blocks."""

    def test_thinking_block_with_thinking_attr(self):
        raw = _FakeMessage(
            content=[
                _FakeBlock(type="thinking", thinking="deep thought"),
                _FakeBlock(type="text", text="the answer is 42"),
            ]
        )
        msg = assistant_message_from_api(raw)
        assert len(msg.content) == 2
        assert isinstance(msg.content[0], ThinkingBlock)
        assert msg.content[0].text == "deep thought"
        assert isinstance(msg.content[1], TextBlock)

    def test_thinking_block_with_text_attr_fallback(self):
        raw = _FakeMessage(
            content=[
                _FakeBlock(type="thinking", text="reasoning via text attr"),
            ]
        )
        msg = assistant_message_from_api(raw)
        assert isinstance(msg.content[0], ThinkingBlock)
        assert msg.content[0].text == "reasoning via text attr"

    def test_no_thinking(self):
        raw = _FakeMessage(
            content=[
                _FakeBlock(type="text", text="plain response"),
            ]
        )
        msg = assistant_message_from_api(raw)
        assert len(msg.content) == 1
        assert isinstance(msg.content[0], TextBlock)


# ---------------------------------------------------------------------------
# OpenAI-compat: _convert_assistant_message with ThinkingBlock
# ---------------------------------------------------------------------------


class TestOpenAIConvertAssistantMessageThinking:
    """Test that ThinkingBlock content is converted to reasoning_content."""

    def test_thinking_becomes_reasoning_content(self):
        msg = ConversationMessage(
            role="assistant",
            content=[
                ThinkingBlock(text="Let me think..."),
                TextBlock(text="The answer."),
            ],
        )
        result = _convert_assistant_message(msg)
        assert result["role"] == "assistant"
        assert result["content"] == "The answer."
        assert result["reasoning_content"] == "Let me think..."

    def test_multiple_thinking_blocks_concatenated(self):
        msg = ConversationMessage(
            role="assistant",
            content=[
                ThinkingBlock(text="Part 1. "),
                ThinkingBlock(text="Part 2."),
                TextBlock(text="Final answer."),
            ],
        )
        result = _convert_assistant_message(msg)
        assert result["reasoning_content"] == "Part 1. Part 2."

    def test_no_thinking_with_tool_calls_has_no_reasoning_key(self):
        msg = ConversationMessage(
            role="assistant",
            content=[
                ToolUseBlock(id="t1", name="bash", input={"cmd": "ls"}),
            ],
        )
        result = _convert_assistant_message(msg)
        assert "reasoning_content" not in result
        assert len(result["tool_calls"]) == 1

    def test_thinking_with_tool_calls(self):
        msg = ConversationMessage(
            role="assistant",
            content=[
                ThinkingBlock(text="I need to check the file"),
                ToolUseBlock(id="t1", name="read_file", input={"path": "/a"}),
            ],
        )
        result = _convert_assistant_message(msg)
        assert result["reasoning_content"] == "I need to check the file"
        assert result["content"] is None
        assert len(result["tool_calls"]) == 1

    def test_no_thinking_no_tools(self):
        msg = ConversationMessage(
            role="assistant",
            content=[TextBlock(text="Simple reply.")],
        )
        result = _convert_assistant_message(msg)
        assert result["content"] == "Simple reply."
        assert "reasoning_content" not in result


# ---------------------------------------------------------------------------
# OpenAI-compat: _convert_messages_to_openai preserves thinking in round-trip
# ---------------------------------------------------------------------------


class TestOpenAIMessagesRoundTripThinking:
    """Test that thinking blocks in messages convert to OpenAI format correctly."""

    def test_assistant_with_thinking_in_conversation(self):
        messages = [
            ConversationMessage.from_user_text("What is 2+2?"),
            ConversationMessage(
                role="assistant",
                content=[
                    ThinkingBlock(text="2+2=4"),
                    TextBlock(text="The answer is 4."),
                ],
            ),
        ]
        result = _convert_messages_to_openai(messages, None)
        assert len(result) == 2
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "assistant"
        assert result[1]["content"] == "The answer is 4."
        assert result[1]["reasoning_content"] == "2+2=4"


# ---------------------------------------------------------------------------
# Stream event types
# ---------------------------------------------------------------------------


class TestThinkingStreamEvents:
    """Test ThinkingDelta and ApiThinkingDeltaEvent."""

    def test_thinking_delta_event(self):
        event = ThinkingDelta(text="reasoning step")
        assert event.text == "reasoning step"

    def test_api_thinking_delta_event(self):
        event = ApiThinkingDeltaEvent(text="api reasoning")
        assert event.text == "api reasoning"
