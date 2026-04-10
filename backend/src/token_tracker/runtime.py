"""Helpers for persisting aggregated run usage."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from providers.types import UsageSnapshot

if TYPE_CHECKING:
    from token_tracker.store import UsageStore

logger = logging.getLogger(__name__)


def persist_run_usage(
    *,
    usage_store: "UsageStore",
    session_id: str | None,
    run_id: str | None,
    agent_name: str,
    model_id: str,
    usage: UsageSnapshot | None,
) -> None:
    """Persist run-linked usage when all required data is available."""
    if (
        not session_id
        or not run_id
        or usage is None
        or not (usage.input_tokens or usage.output_tokens)
    ):
        return

    try:
        usage_store.record(
            session_id=session_id,
            run_id=run_id,
            agent_name=agent_name,
            model_id=model_id,
            prompt_tokens=usage.input_tokens,
            completion_tokens=usage.output_tokens,
        )
    except Exception:
        logger.debug(
            "Failed to persist token usage for run %s (agent=%s)",
            run_id,
            agent_name,
            exc_info=True,
        )
