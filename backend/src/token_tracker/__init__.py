"""Token tracker module for recording and querying token usage."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from token_tracker.models import TokenUsageRecord
    from token_tracker.store import UsageStore

__all__ = ["TokenUsageRecord", "UsageStore", "TokenTracker"]


def __getattr__(name: str) -> Any:
    if name == "TokenUsageRecord":
        return import_module("token_tracker.models").TokenUsageRecord
    if name == "UsageStore":
        return import_module("token_tracker.store").UsageStore
    raise AttributeError(name)


class TokenTracker:
    """High-level token usage tracker."""

    def __init__(self) -> None:
        self._store = import_module("token_tracker.store").UsageStore()

    @property
    def store(self) -> "UsageStore":
        """Direct access to underlying UsageStore for compatibility."""
        return self._store

    def initialize(self, session_factory) -> None:
        """Initialize the underlying store with a session factory."""
        self._store.initialize(session_factory)

    def record(
        self,
        *,
        session_id: str,
        run_id: str | None = None,
        agent_name: str,
        model_id: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
    ) -> "TokenUsageRecord":
        """Record token usage for an agent call."""
        return self._store.record(
            session_id=session_id,
            run_id=run_id,
            agent_name=agent_name,
            model_id=model_id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    def get_session_usage(self, session_id: str) -> dict:
        """Get aggregated usage for a session."""
        return self._store.get_session_usage(session_id)

    def get_usage_by_model(self, session_id: str | None = None) -> list[dict]:
        """Get usage breakdown by model, optionally filtered by session."""
        return self._store.get_usage_by_model(session_id)

    def get_run_usage(self, run_id: str) -> dict | None:
        """Get aggregated usage for a single run."""
        return self._store.get_run_usage(run_id)

    def get_usage_for_runs(self, run_ids: list[str]) -> dict[str, dict]:
        """Get aggregated usage for multiple runs keyed by ``run_id``."""
        return self._store.get_usage_for_runs(run_ids)
