"""Public notification API."""

from notification._metadata import (
    SYSTEM_NOTIFICATIONS_METADATA_KEY,
    serialize_system_notifications,
)
from notification._rule_catalog import make_budget_warning, make_opening_reminder
from notification._rule_engine import NotificationRule, dispatch_rules
from notification._runtime import SystemNotification
from notification._runtime import SystemNotificationService

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
