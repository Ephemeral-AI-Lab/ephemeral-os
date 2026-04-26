"""System notification service used by agent runs, tool execution, and hooks."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from message.messages import SystemNotificationBlock
from notification.events import SystemNotification


@dataclass
class SystemNotificationService:
    """Run-scoped notification sink for hooks, tools, and runtime code.

    Agent runs emit notifications as stream events only; standalone tool
    executions can still pass ``emit`` or drain notifications into tool
    metadata for backwards compatibility.
    """

    emit: Callable[[SystemNotification], Awaitable[None]] | None = None
    _registered_agent_run: bool = field(default=False, init=False, repr=False)
    _notifications: list[SystemNotificationBlock] = field(default_factory=list, repr=False)
    _events: list[SystemNotification] = field(default_factory=list, init=False, repr=False)

    @property
    def has_registered_agent_run(self) -> bool:
        """Return True when this service is owned by an agent run."""
        return self._registered_agent_run

    def register_agent_run(self) -> None:
        """Mark the service as owned by a live agent run."""
        self._registered_agent_run = True

    async def notify_system(self, text: str, *, category: str = "") -> None:
        if not text:
            return
        event = SystemNotification(text=text, category=category)
        self._notifications.append(SystemNotificationBlock(text=text, category=category))
        if self.emit is not None:
            await self.emit(event)
        else:
            self._events.append(event)

    async def notify(self, text: str, *, category: str = "") -> None:
        await self.notify_system(text, category=category)

    def flush_events(self) -> list[SystemNotification]:
        """Return pending notifications without appending transcript messages."""
        events = list(self._events)
        if not events and self._notifications:
            events = [
                SystemNotification(text=notification.text, category=notification.category)
                for notification in self._notifications
            ]
        self._notifications.clear()
        self._events.clear()
        return events

    def pop_pending_notifications(self) -> list[SystemNotificationBlock]:
        notifications = list(self._notifications)
        self._notifications.clear()
        self._events.clear()
        return notifications
