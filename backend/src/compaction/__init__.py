"""Conversation compaction — microcompact and full LLM-based summarization."""

from compaction.compactor import (
    AUTOCOMPACT_BUFFER_TOKENS,
    COMPACTABLE_TOOLS,
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

from compaction.compactor import compact_for_api  # noqa: F401

__all__ = [
    "AUTOCOMPACT_BUFFER_TOKENS",
    "COMPACTABLE_TOOLS",
    "MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES",
    "MAX_OUTPUT_TOKENS_FOR_SUMMARY",
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
