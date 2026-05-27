"""Core model types — request/response dataclasses, streaming protocol, usage tracking."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol
from collections.abc import AsyncIterator

from pydantic import BaseModel

if TYPE_CHECKING:
    from message import Message
    from message.events import StreamEvent


# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------


class UsageSnapshot(BaseModel):
    """Token usage returned by the model provider."""

    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        """Return the total number of accounted tokens."""
        return self.input_tokens + self.output_tokens


# ---------------------------------------------------------------------------
# Message request
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MessageRequest:
    """Input parameters for a model invocation."""

    model: str
    messages: list[Message] = field(default_factory=list)
    system_prompt: str | None = None
    max_tokens: int = 32768
    tools: list[dict[str, Any]] = field(default_factory=list)
    tool_choice: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Streaming protocol
# ---------------------------------------------------------------------------


class SupportsStreamingMessages(Protocol):
    """Protocol used by the query engine in tests and production."""

    def stream_message(self, request: MessageRequest) -> AsyncIterator[StreamEvent]:
        """Yield streamed events for the request."""
