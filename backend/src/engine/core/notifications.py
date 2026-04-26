"""Notification plumbing shared by the query loop."""

from __future__ import annotations

from message.messages import ConversationMessage
from message.stream_events import StreamEvent
from notification.service import SystemNotificationService
from providers.types import UsageSnapshot
from tools.core.runtime import ExecutionMetadata


def ensure_system_notification_service(
    metadata: ExecutionMetadata | None,
    messages: list[ConversationMessage],
) -> SystemNotificationService:
    service = metadata.system_notification_service if metadata is not None else None
    if not isinstance(service, SystemNotificationService):
        service = SystemNotificationService()
        if metadata is not None:
            metadata.system_notification_service = service
    service.register_messages(messages)
    return service


def flush_system_notifications(
    service: SystemNotificationService,
) -> list[tuple[StreamEvent, UsageSnapshot | None]]:
    _message, events = service.flush_to_messages()
    return [(event, None) for event in events]
