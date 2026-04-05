"""Lightweight event bus with middleware pipeline for coordination lifecycle.

Adapted from Synthetic OS. Supports synchronous subscribers and middleware
that can transform, enrich, or suppress events before delivery.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)


class Middleware:
    """Base class for coordination lifecycle middleware.

    Subclass and override hook methods to intercept lifecycle events.
    Each hook receives the event dict and returns it (possibly modified)
    or None to suppress the event.
    """

    def on_event(self, event: dict[str, Any]) -> dict[str, Any] | None:
        """Generic catch-all hook. Delegates to typed hooks by default."""
        event_type = event.get("type", "")
        typed_hook = getattr(self, f"on_{event_type}", None)
        if callable(typed_hook):
            return typed_hook(event)
        return event

    # -- Typed hooks (override as needed) ----------------------------------

    def on_task_dispatched(self, event: dict[str, Any]) -> dict[str, Any] | None:
        return event

    def on_task_completed(self, event: dict[str, Any]) -> dict[str, Any] | None:
        return event

    def on_task_failed(self, event: dict[str, Any]) -> dict[str, Any] | None:
        return event

    def on_run_finalized(self, event: dict[str, Any]) -> dict[str, Any] | None:
        return event

    def on_worker_dispatched(self, event: dict[str, Any]) -> dict[str, Any] | None:
        return event

    def on_worker_terminal(self, event: dict[str, Any]) -> dict[str, Any] | None:
        return event

    def on_cascade_started(self, event: dict[str, Any]) -> dict[str, Any] | None:
        return event

    def on_file_claimed(self, event: dict[str, Any]) -> dict[str, Any] | None:
        return event

    def on_file_released(self, event: dict[str, Any]) -> dict[str, Any] | None:
        return event


class EventBus:
    """Pub/sub event bus with middleware pipeline.

    Events are plain dicts with at least a "type" key. Predefined types:

    - task_dispatched:  {run_id, task_id, agent_name}
    - task_completed:   {run_id, task_id, summary}
    - task_failed:      {run_id, task_id, error}
    - worker_dispatched:{run_id, task_id, agent_name, model_key}
    - worker_terminal:  {run_id, task_id, status, summary|error}
    - run_finalized:    {run_id, status}
    - cascade_started:  {run_id, round}
    - file_claimed:     {run_id, task_id, filepath, agent_id, mode}
    - file_released:    {run_id, task_id, filepath, agent_id}
    """

    def __init__(self, *, keep_history: bool = False) -> None:
        self._subscribers: dict[str, list[Callable[[dict[str, Any]], None]]] = {}
        self._middleware: list[Middleware] = []
        self._keep_history = keep_history
        self._history: list[dict[str, Any]] = []

    def use(self, middleware: Middleware) -> Callable[[], None]:
        """Register a Middleware instance. Returns an unregister callable."""
        self._middleware.append(middleware)

        def _remove() -> None:
            try:
                self._middleware.remove(middleware)
            except ValueError:
                pass

        return _remove

    def subscribe(
        self,
        event_type: str,
        handler: Callable[[dict[str, Any]], None],
    ) -> Callable[[], None]:
        """Register handler for event_type. Returns an unsubscribe callable."""
        self._subscribers.setdefault(event_type, []).append(handler)

        def _unsub() -> None:
            try:
                self._subscribers[event_type].remove(handler)
            except (KeyError, ValueError):
                pass

        return _unsub

    def subscribe_all(
        self,
        handler: Callable[[dict[str, Any]], None],
    ) -> Callable[[], None]:
        """Register handler for every event type (wildcard)."""
        return self.subscribe("*", handler)

    def emit(self, event: dict[str, Any]) -> None:
        """Run event through middleware pipeline, then dispatch to subscribers."""
        processed: dict[str, Any] | None = event
        for mw in self._middleware:
            try:
                processed = mw.on_event(processed)  # type: ignore[arg-type]
            except Exception:
                logger.debug(
                    "Middleware %s failed for event %s",
                    type(mw).__name__,
                    event.get("type", ""),
                    exc_info=True,
                )
        if processed is None:
            return

        if self._keep_history:
            self._history.append(processed)

        event_type = processed.get("type", "")
        for handler in self._subscribers.get(event_type, []):
            try:
                handler(processed)
            except Exception:
                logger.debug(
                    "Event handler failed for %s", event_type, exc_info=True
                )
        for handler in self._subscribers.get("*", []):
            try:
                handler(processed)
            except Exception:
                logger.debug(
                    "Wildcard event handler failed for %s",
                    event_type,
                    exc_info=True,
                )

    @property
    def history(self) -> list[dict[str, Any]]:
        return list(self._history)

    def clear(self) -> None:
        """Remove all subscribers and middleware."""
        self._subscribers.clear()
        self._middleware.clear()
        self._history.clear()
