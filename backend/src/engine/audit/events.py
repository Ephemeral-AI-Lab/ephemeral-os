"""Engine audit event type constants."""

from __future__ import annotations

TOOL_STARTED = "engine.tool.started"
TOOL_COMPLETED = "engine.tool.completed"
TOOL_FAILED = "engine.tool.failed"

__all__ = [
    "TOOL_COMPLETED",
    "TOOL_FAILED",
    "TOOL_STARTED",
]
