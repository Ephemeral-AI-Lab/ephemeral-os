"""Public notification API."""

from notification.runtime import SystemNotification, SystemNotificationService
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
    "make_budget_warning",
    "make_opening_reminder",
    "serialize_system_notifications",
]
