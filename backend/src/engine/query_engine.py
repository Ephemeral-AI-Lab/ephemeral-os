"""High-level conversation engine."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, AsyncIterator

if TYPE_CHECKING:
    from utils.compact import SessionState

from models.types import SupportsStreamingMessages
from engine.cost_tracker import CostTracker
from engine.messages import ConversationMessage
from engine.query import QueryContext, run_query
from engine.stream_events import StreamEvent
from hooks import HookExecutor
from tools.base import ToolRegistry


class QueryEngine:
    """Owns conversation history and the tool-aware model loop."""

    def __init__(
        self,
        *,
        api_client: SupportsStreamingMessages,
        tool_registry: ToolRegistry,
        cwd: str | Path,
        model: str,
        system_prompt: str,
        max_tokens: int = 4096,
        max_turns: int = 200,
        hook_executor: HookExecutor | None = None,
        tool_metadata: dict[str, object] | None = None,
        session_state: "SessionState | None" = None,
    ) -> None:
        self._api_client = api_client
        self._tool_registry = tool_registry
        self._cwd = Path(cwd).resolve()
        self._model = model
        self._system_prompt = system_prompt
        self._max_tokens = max_tokens
        self._max_turns = max_turns
        self._hook_executor = hook_executor
        self._tool_metadata = tool_metadata or {}
        self._session_state = session_state
        self._messages: list[ConversationMessage] = []
        self._cost_tracker = CostTracker()

    @property
    def messages(self) -> list[ConversationMessage]:
        """Return the current conversation history."""
        return list(self._messages)

    @property
    def session_state(self):
        """Return the current compaction state (persisted across agents)."""
        return self._session_state

    @property
    def total_usage(self):
        """Return the total usage across all turns."""
        return self._cost_tracker.total

    def clear(self) -> None:
        """Clear the in-memory conversation history."""
        self._messages.clear()
        self._cost_tracker = CostTracker()

    def load_messages(self, messages: list[ConversationMessage]) -> None:
        """Replace the in-memory conversation history."""
        self._messages = list(messages)

    async def submit_message(self, prompt: str) -> AsyncIterator[StreamEvent]:
        """Append a user message and execute the query loop."""
        self._messages.append(ConversationMessage.from_user_text(prompt))
        context = QueryContext(
            api_client=self._api_client,
            tool_registry=self._tool_registry,
            cwd=self._cwd,
            model=self._model,
            system_prompt=self._system_prompt,
            max_tokens=self._max_tokens,
            max_turns=self._max_turns,
            hook_executor=self._hook_executor,
            tool_metadata=self._tool_metadata,
            session_state=self._session_state,
        )
        messages, event_stream = await run_query(context, self._messages)
        self._messages = messages
        async for event, usage in event_stream:
            if usage is not None:
                self._cost_tracker.add(usage)
            yield event
