"""Live end-to-end tests for the Anthropic native client.

Requires a real ANTHROPIC_API_KEY environment variable.
Run with: pytest tests/test_e2e/test_anthropic_live.py -m live -v
"""

from __future__ import annotations

import os

import pytest

from models.clients.anthropic_native import AnthropicClient
from models.types import (
    ApiMessageRequest,
    ApiTextDeltaEvent,
    ApiToolUseDeltaEvent,
    ApiMessageCompleteEvent,
    ApiThinkingDeltaEvent,
)
from engine.messages import ConversationMessage

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = "claude-sonnet-4-20250514"

pytestmark = [pytest.mark.e2e, pytest.mark.live]


@pytest.fixture
def client():
    if not ANTHROPIC_API_KEY:
        pytest.skip("ANTHROPIC_API_KEY not set")
    return AnthropicClient(api_key=ANTHROPIC_API_KEY)


def _user_message(text: str) -> ConversationMessage:
    """Build a minimal user ConversationMessage."""
    return ConversationMessage.from_user_text(text)


# ---------------------------------------------------------------------------
# 1. Simple text response
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_simple_text_response(client):
    """Stream a simple reply and verify text delta + complete events."""
    request = ApiMessageRequest(
        model=MODEL,
        messages=[_user_message("Say hello in exactly 3 words")],
        max_tokens=64,
    )

    events = []
    async for event in client.stream_message(request):
        events.append(event)

    text_events = [e for e in events if isinstance(e, ApiTextDeltaEvent)]
    complete_events = [e for e in events if isinstance(e, ApiMessageCompleteEvent)]

    assert len(text_events) >= 1, "Expected at least one ApiTextDeltaEvent"
    assert len(complete_events) == 1, "Expected exactly one ApiMessageCompleteEvent"
    assert complete_events[-1] is events[-1], "ApiMessageCompleteEvent must be the last event"

    full_text = "".join(e.text for e in text_events)
    assert full_text.strip(), "Final text must be non-empty"


# ---------------------------------------------------------------------------
# 2. Tool use mid-stream ordering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_use_mid_stream_ordering(client):
    """Validate that ApiToolUseDeltaEvent arrives BEFORE ApiMessageCompleteEvent."""
    weather_tool = {
        "name": "get_weather",
        "description": "Get weather for a city",
        "input_schema": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    }

    request = ApiMessageRequest(
        model=MODEL,
        messages=[_user_message("What's the weather in Tokyo? Use the get_weather tool.")],
        tools=[weather_tool],
        max_tokens=256,
    )

    events = []
    async for event in client.stream_message(request):
        events.append(event)

    tool_events = [e for e in events if isinstance(e, ApiToolUseDeltaEvent)]
    complete_events = [e for e in events if isinstance(e, ApiMessageCompleteEvent)]

    assert len(tool_events) >= 1, "Expected at least one ApiToolUseDeltaEvent"
    assert len(complete_events) == 1, "Expected exactly one ApiMessageCompleteEvent"

    # Tool event must appear before complete event in the stream
    first_tool_idx = next(i for i, e in enumerate(events) if isinstance(e, ApiToolUseDeltaEvent))
    complete_idx = next(i for i, e in enumerate(events) if isinstance(e, ApiMessageCompleteEvent))
    assert first_tool_idx < complete_idx, (
        f"Tool event at index {first_tool_idx} must precede complete event at index {complete_idx}"
    )

    tool_event = tool_events[0]
    assert tool_event.name == "get_weather", f"Expected tool name 'get_weather', got '{tool_event.name}'"
    assert "city" in tool_event.input, f"Expected 'city' in tool input, got {tool_event.input}"


# ---------------------------------------------------------------------------
# 3. Multiple tools arrive in order
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multiple_tools_arrive_in_order(client):
    """Two tool calls should arrive sequentially, both before ApiMessageCompleteEvent."""
    weather_tool = {
        "name": "get_weather",
        "description": "Get weather for a city",
        "input_schema": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    }
    time_tool = {
        "name": "get_time",
        "description": "Get the current time in a city",
        "input_schema": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    }

    request = ApiMessageRequest(
        model=MODEL,
        messages=[
            _user_message(
                "Get the weather in Tokyo and the current time in London. Use both tools."
            )
        ],
        tools=[weather_tool, time_tool],
        max_tokens=512,
    )

    events = []
    async for event in client.stream_message(request):
        events.append(event)

    tool_events = [e for e in events if isinstance(e, ApiToolUseDeltaEvent)]
    complete_events = [e for e in events if isinstance(e, ApiMessageCompleteEvent)]

    assert len(tool_events) == 2, f"Expected 2 ApiToolUseDeltaEvent, got {len(tool_events)}"
    assert len(complete_events) == 1, "Expected exactly one ApiMessageCompleteEvent"

    # Both tool events must appear before the complete event
    tool_indices = [i for i, e in enumerate(events) if isinstance(e, ApiToolUseDeltaEvent)]
    complete_idx = next(i for i, e in enumerate(events) if isinstance(e, ApiMessageCompleteEvent))

    for idx in tool_indices:
        assert idx < complete_idx, (
            f"Tool event at index {idx} must precede complete event at index {complete_idx}"
        )

    # First tool event index must be less than second — sequential mid-stream
    assert tool_indices[0] < tool_indices[1], (
        "First tool event must arrive before second tool event"
    )


# ---------------------------------------------------------------------------
# 4. Usage reported
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_usage_reported(client):
    """ApiMessageCompleteEvent must contain positive token usage counters."""
    request = ApiMessageRequest(
        model=MODEL,
        messages=[_user_message("What is 2 + 2?")],
        max_tokens=64,
    )

    events = []
    async for event in client.stream_message(request):
        events.append(event)

    complete_events = [e for e in events if isinstance(e, ApiMessageCompleteEvent)]
    assert len(complete_events) == 1, "Expected exactly one ApiMessageCompleteEvent"

    usage = complete_events[0].usage
    assert usage.input_tokens > 0, f"input_tokens must be > 0, got {usage.input_tokens}"
    assert usage.output_tokens > 0, f"output_tokens must be > 0, got {usage.output_tokens}"


# ---------------------------------------------------------------------------
# 5. Provider routing
# ---------------------------------------------------------------------------


def test_provider_routing():
    """make_api_client returns AnthropicClient when api_format='anthropic'."""
    if not ANTHROPIC_API_KEY:
        pytest.skip("ANTHROPIC_API_KEY not set")

    from config.settings import Settings
    from models.provider import make_api_client

    settings = Settings(
        api_key=ANTHROPIC_API_KEY,
        api_format="anthropic",
        model=MODEL,
    )
    resolved_client = make_api_client(settings)
    assert isinstance(resolved_client, AnthropicClient), (
        f"Expected AnthropicClient, got {type(resolved_client).__name__}"
    )
