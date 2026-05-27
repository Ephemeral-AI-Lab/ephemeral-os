"""Shared helper: construct a synthetic ``ask_advisor`` approval transcript pair.

The mock squad bypasses the engine loop, so it has no real ``ask_advisor``
result to thread through ``conversation_messages`` when a gated terminal
fires. Without intervention every existing live e2e scenario would trip
``AdvisorApprovalPreHook``. This helper produces the same two-message pair
the engine would have produced (assistant ``ToolUseBlock`` paired with a
user ``ToolResultBlock`` carrying ``metadata.helper_role == "advisor"``).

Lives under ``src/`` so the mock runner — the legitimate src-side consumer —
can import it without a test→src layering inversion. Unit tests re-export
the same symbol from
``backend/tests/unit_test/test_tools/test_submission/_advisor_approval_fixtures.py``.
"""

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
    """Return ``[assistant(ask_advisor), user(advisor result)]`` pair.

    Mirrors what the engine produces when an agent calls ``ask_advisor`` and
    the advisor responds via ``submit_advisor_feedback``. Callers prepend this
    pair to whatever ``conversation_messages`` they thread to a gated terminal.
    """
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
