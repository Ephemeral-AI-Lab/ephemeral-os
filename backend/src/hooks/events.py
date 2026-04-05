"""Hook event names supported by EphemeralOS."""

from __future__ import annotations

from enum import Enum


class HookEvent(str, Enum):
    """Events that can trigger hooks."""

    SESSION_START = "session_start"
    SESSION_END = "session_end"
    PRE_TOOL_USE = "pre_tool_use"
    POST_TOOL_USE = "post_tool_use"
    PIPELINE_STEP_COMPLETE = "pipeline_step_complete"
    PIPELINE_STEP_FAILED = "pipeline_step_failed"
