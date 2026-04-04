"""Core engine exports."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from ephemeralos.engine.messages import (
        ConversationMessage,
        TextBlock,
        ToolResultBlock,
        ToolUseBlock,
    )
    from ephemeralos.engine.query_engine import QueryEngine
    from ephemeralos.engine.stream_events import (
        AssistantTextDelta,
        AssistantTurnComplete,
        ToolExecutionCompleted,
        ToolExecutionStarted,
    )

__all__ = [
    "AssistantTextDelta",
    "AssistantTurnComplete",
    "ConversationMessage",
    "QueryEngine",
    "TextBlock",
    "ToolExecutionCompleted",
    "ToolExecutionStarted",
    "ToolResultBlock",
    "ToolUseBlock",
]


def __getattr__(name: str):
    if name in {"ConversationMessage", "TextBlock", "ToolResultBlock", "ToolUseBlock"}:
        from ephemeralos.engine.messages import (
            ConversationMessage,
            TextBlock,
            ToolResultBlock,
            ToolUseBlock,
        )

        return {
            "ConversationMessage": ConversationMessage,
            "TextBlock": TextBlock,
            "ToolResultBlock": ToolResultBlock,
            "ToolUseBlock": ToolUseBlock,
        }[name]

    if name == "QueryEngine":
        from ephemeralos.engine.query_engine import QueryEngine

        return QueryEngine

    if name in {
        "AssistantTextDelta",
        "AssistantTurnComplete",
        "ToolExecutionCompleted",
        "ToolExecutionStarted",
    }:
        from ephemeralos.engine.stream_events import (
            AssistantTextDelta,
            AssistantTurnComplete,
            ToolExecutionCompleted,
            ToolExecutionStarted,
        )

        return {
            "AssistantTextDelta": AssistantTextDelta,
            "AssistantTurnComplete": AssistantTurnComplete,
            "ToolExecutionCompleted": ToolExecutionCompleted,
            "ToolExecutionStarted": ToolExecutionStarted,
        }[name]

    raise AttributeError(name)
