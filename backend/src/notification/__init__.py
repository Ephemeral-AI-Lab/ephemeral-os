"""Public notification API."""

from notification.runtime import (
    SystemNotification,
    SystemNotificationService,
    ensure_system_notification_service,
    flush_system_notification_events,
)
from notification.metadata import (
    SYSTEM_NOTIFICATIONS_METADATA_KEY,
    serialize_system_notifications,
)
from notification.rules import NotificationRule, dispatch_rules
from notification.rules import make_budget_warning, make_opening_reminder

__all__ = [
    "NotificationRule",
    "SYSTEM_NOTIFICATIONS_METADATA_KEY",
    "SystemNotification",
    "SystemNotificationService",
    "dispatch_rules",
    "ensure_system_notification_service",
    "flush_system_notification_events",
    "make_budget_warning",
    "make_opening_reminder",
    "serialize_system_notifications",
]
