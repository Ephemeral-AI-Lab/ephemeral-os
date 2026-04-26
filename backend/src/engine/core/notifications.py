"""Notification plumbing shared by the query loop."""

from __future__ import annotations

from typing import Protocol

from engine.core.turn_request import QueryTurnRequest, record_system_notification_message
from message.messages import ConversationMessage
from message.stream_events import StreamEvent
from notification.service import SystemNotificationService
from providers.types import UsageSnapshot
from tools.core.runtime import ExecutionMetadata


class HasToolMetadata(Protocol):
    tool_metadata: ExecutionMetadata | None


def ensure_system_notification_service(
    context: HasToolMetadata,
    messages: list[ConversationMessage],
) -> SystemNotificationService:
    service = (
        context.tool_metadata.system_notification_service
        if context.tool_metadata is not None
        else None
    )
    if not isinstance(service, SystemNotificationService):
        service = SystemNotificationService()
        if context.tool_metadata is not None:
            context.tool_metadata.system_notification_service = service
    service.register_messages(messages)
    return service


def flush_system_notifications(
    service: SystemNotificationService,
    *,
    turn: QueryTurnRequest | None = None,
) -> list[tuple[StreamEvent, UsageSnapshot | None]]:
    message, events = service.flush_to_messages()
    if message is not None and turn is not None:
        record_system_notification_message(turn, message)
    return [(event, None) for event in events]
