"""Engine audit event type constants."""

from __future__ import annotations

AGENT_STARTED = "engine.agent.started"
AGENT_COMPLETED = "engine.agent.completed"
AGENT_FAILED = "engine.agent.failed"

TOOL_REQUESTED = "engine.tool.requested"
TOOL_STARTED = "engine.tool.started"
TOOL_REJECTED = "engine.tool.rejected"
TOOL_COMPLETED = "engine.tool.completed"
TOOL_FAILED = "engine.tool.failed"

__all__ = [
    "AGENT_COMPLETED",
    "AGENT_FAILED",
    "AGENT_STARTED",
    "TOOL_COMPLETED",
    "TOOL_FAILED",
    "TOOL_REJECTED",
    "TOOL_REQUESTED",
    "TOOL_STARTED",
]
