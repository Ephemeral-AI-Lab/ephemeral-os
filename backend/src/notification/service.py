"""System notification service used by agent runs, tool execution, and hooks."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from message.messages import ConversationMessage, SystemNotificationBlock
from notification.events import SystemNotification


@dataclass
class SystemNotificationService:
    """Run-scoped notification sink for hooks, tools, and runtime code.

    Notifications are retained as ``SystemNotificationBlock`` objects so the query
    loop can append them to the durable message list at provider-safe points.
    Standalone tool executions can still pass ``emit`` and drain notifications into
    tool metadata for backwards compatibility.
    """

    emit: Callable[[SystemNotification], Awaitable[None]] | None = None
    _messages: list[ConversationMessage] | None = field(default=None, init=False, repr=False)
    _notifications: list[SystemNotificationBlock] = field(default_factory=list, repr=False)
    _events: list[SystemNotification] = field(default_factory=list, init=False, repr=False)

    @property
    def has_registered_messages(self) -> bool:
        """Return True when this service is bound to an agent message list."""
        return self._messages is not None

    def register_messages(self, messages: list[ConversationMessage]) -> None:
        """Bind the service to the live message history for one agent run."""
        self._messages = messages

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

    def flush_to_messages(self) -> tuple[ConversationMessage | None, list[SystemNotification]]:
        """Append pending notifications to the registered message list.

        Returns the appended message and any notification events that were not
        already emitted through ``emit``.
        """
        if not self._notifications:
            return None, []
        notifications = list(self._notifications)
        events = list(self._events)
        self._notifications.clear()
        self._events.clear()
        message = ConversationMessage(role="user", content=notifications)
        if self._messages is not None:
            self._messages.append(message)
        return message, events

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
