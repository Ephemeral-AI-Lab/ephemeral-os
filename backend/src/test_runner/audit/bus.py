"""AuditEventBus — in-memory synchronous fanout for audit Events."""

from __future__ import annotations

from collections.abc import Callable

from test_runner.audit.events import Event

__all__ = ["AuditEventBus"]


class AuditEventBus:
    """Synchronous fanout bus. Single-threaded; no locking."""

    def __init__(self) -> None:
        self._handlers: list[Callable[[Event], None]] = []
        self.errors: list[tuple[Event, BaseException]] = []

    def publish(self, event: Event) -> None:
        """Fire all handlers in subscription order. Errors are collected, not re-raised."""
        for handler in list(self._handlers):
            try:
                handler(event)
            except BaseException as exc:  # noqa: BLE001
                self.errors.append((event, exc))

    def subscribe(self, handler: Callable[[Event], None]) -> Callable[[], None]:
        """Append handler and return an unsubscribe callable."""
        self._handlers.append(handler)

        def _unsubscribe() -> None:
            try:
                self._handlers.remove(handler)
            except ValueError:
                pass

        return _unsubscribe
