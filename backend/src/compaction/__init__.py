"""Conversation compaction — microcompact and full LLM-based summarization."""

from compaction.compactor import (
    AUTOCOMPACT_BUFFER_TOKENS,
    MAX_OUTPUT_TOKENS_FOR_SUMMARY,
    MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES,
    TIME_BASED_MC_CLEARED_MESSAGE,
    SessionState,
    build_compact_summary_message,
    estimate_message_tokens,
    format_compact_summary,
    get_autocompact_threshold,
    get_compact_prompt,
    get_context_window,
    microcompact_messages,
    should_autocompact,
)

try:
    from compaction.compactor import auto_compact_if_needed  # noqa: F401
except ImportError:
    pass

__all__ = [
    "AUTOCOMPACT_BUFFER_TOKENS",
    "MAX_OUTPUT_TOKENS_FOR_SUMMARY",
    "MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES",
    "TIME_BASED_MC_CLEARED_MESSAGE",
    "SessionState",
    "build_compact_summary_message",
    "estimate_message_tokens",
    "format_compact_summary",
    "get_autocompact_threshold",
    "get_compact_prompt",
    "get_context_window",
    "microcompact_messages",
    "should_autocompact",
]
