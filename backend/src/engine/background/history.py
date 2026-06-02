"""Provider-history hook for typed background surfaces."""

from __future__ import annotations

from message import Message


def reduce_background_task_history(
    messages: list[Message],
) -> list[Message]:
    """Return messages unchanged; typed controls no longer emit reducible snapshots."""
    return messages


__all__ = ["reduce_background_task_history"]
