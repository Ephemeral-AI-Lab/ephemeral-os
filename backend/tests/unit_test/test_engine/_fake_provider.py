"""Test-only fake :class:`SupportsStreamingMessages` for engine-loop tests.

The retry path in :func:`engine.api.run_ephemeral_agent` wraps the real
query loop. To exercise that loop end-to-end without a live provider we
script a sequence of provider turns and let the loop drive real tool
dispatch, real budget bookkeeping, and the real retry transcript.

Each :class:`ScriptedTurn` describes one ``stream_message`` invocation:

- ``text_deltas`` are streamed as :class:`ApiTextDeltaEvent` events first.
- ``tool_uses`` are streamed as :class:`ApiToolUseDeltaEvent` events (so the
  loop calls ``_count_tool_dispatch`` for each before the message
  completes).
- A trailing :class:`ApiMessageCompleteEvent` carries the assembled assistant
  message containing matching :class:`ToolUseBlock` and :class:`TextBlock`
  entries plus the turn's :class:`UsageSnapshot`.

When the provider is asked to stream more turns than were scripted the
client raises an :class:`AssertionError` so retry-coverage tests fail
loudly rather than silently degrade into a single-turn happy path.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from message.messages import ConversationMessage, TextBlock, ToolUseBlock
from providers.types import (
    ApiMessageCompleteEvent,
    ApiMessageRequest,
    ApiStreamEvent,
    ApiTextDeltaEvent,
    ApiToolUseDeltaEvent,
    UsageSnapshot,
)


@dataclass(frozen=True)
class ScriptedToolUse:
    """One assistant tool_use call to emit on a turn."""

    id: str
    name: str
    input: dict[str, Any] = field(default_factory=dict)


@dataclass
class ScriptedTurn:
    """Description of one ``stream_message`` invocation."""

    text_deltas: tuple[str, ...] = ()
    tool_uses: tuple[ScriptedToolUse, ...] = ()
    usage: UsageSnapshot = field(default_factory=UsageSnapshot)
    # Optional stop_reason on the ApiMessageCompleteEvent — most tests
    # don't care, but a few assertions inspect it.
    stop_reason: str | None = None


class FakeProviderClient:
    """Drives :func:`run_query` from a pre-scripted turn list.

    The :class:`SupportsStreamingMessages` protocol is single-method
    (``stream_message``). Each call pops one :class:`ScriptedTurn` and yields
    its delta events followed by a single
    :class:`ApiMessageCompleteEvent`. ``calls`` records every request
    issued so tests can assert how many provider turns ran (and what the
    transcript looked like at each invocation).
    """

    def __init__(self, turns: list[ScriptedTurn]) -> None:
        self._turns = list(turns)
        self.calls: list[ApiMessageRequest] = []

    @property
    def remaining_turns(self) -> int:
        return len(self._turns)

    async def stream_message(
        self, request: ApiMessageRequest
    ) -> AsyncIterator[ApiStreamEvent]:
        # NOTE: this is intentionally an *async generator function* rather
        # than ``async def ... return self._iter_turn(...)`` — the loop
        # does ``async for event in client.stream_message(request)`` and
        # needs the call to yield an async iterator directly. Wrapping
        # the body with ``yield`` makes Python construct one for us.
        if not self._turns:
            raise AssertionError(
                f"FakeProviderClient: stream_message called for an unscripted "
                f"turn (consumed {len(self.calls)} already). Add another "
                f"ScriptedTurn to the test if the retry was intentional."
            )
        self.calls.append(request)
        turn = self._turns.pop(0)
        for text in turn.text_deltas:
            yield ApiTextDeltaEvent(text=text)
        for tool_use in turn.tool_uses:
            yield ApiToolUseDeltaEvent(
                id=tool_use.id,
                name=tool_use.name,
                input=dict(tool_use.input),
            )
        # Assemble the final assistant message — content blocks must mirror
        # the deltas above so the dispatch branch in :func:`run_query` sees
        # a well-formed ``final_message.tool_uses`` view.
        content: list[Any] = []
        text_blob = "".join(turn.text_deltas)
        if text_blob:
            content.append(TextBlock(text=text_blob))
        for tool_use in turn.tool_uses:
            content.append(
                ToolUseBlock(
                    id=tool_use.id,
                    name=tool_use.name,
                    input=dict(tool_use.input),
                )
            )
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=content),
            usage=turn.usage,
            stop_reason=turn.stop_reason,
        )
