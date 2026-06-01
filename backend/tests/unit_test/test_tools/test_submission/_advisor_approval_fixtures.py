"""Shared fixture: construct a synthetic ask_advisor approval transcript pair."""

from __future__ import annotations

from message.message import (
    Message,
    ToolResultBlock,
    ToolUseBlock,
)


_DEFAULT_ID = "toolu_test_advisor_approval"


def build_advisor_approval_messages(
    *,
    tool_name: str,
    verdict: str = "approve",
    summary: str = "ok",
    tool_payload: dict | None = None,
    tool_use_id: str = _DEFAULT_ID,
    is_error: bool = False,
) -> list[Message]:
    """Return the engine-style ``ask_advisor`` call/result message pair."""
    return [
        Message(
            role="assistant",
            content=[
                ToolUseBlock(
                    tool_use_id=tool_use_id,
                    name="ask_advisor",
                    input={
                        "tool_name": tool_name,
                        "tool_payload": tool_payload or {},
                    },
                )
            ],
        ),
        Message(
            role="user",
            content=[
                ToolResultBlock(
                    tool_use_id=tool_use_id,
                    content=summary,
                    is_error=is_error,
                    metadata={"helper_role": "advisor", "verdict": verdict},
                )
            ],
        ),
    ]


__all__ = ["build_advisor_approval_messages"]
