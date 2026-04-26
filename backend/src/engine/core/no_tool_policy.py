"""Policy for assistant responses that contain no tool calls."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NoToolResponseOutcome:
    exit_text_response: bool = True


def handle_no_tool_response() -> NoToolResponseOutcome:
    """A plain assistant response ends the single-request agent run."""
    return NoToolResponseOutcome()
