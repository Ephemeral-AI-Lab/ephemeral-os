"""Notification rule API."""

from notification.rules.dispatch import dispatch_rules
from notification.rules.factories import (
    make_terminal_call_reminder,
)
from notification.rules.model import MessageList, NotificationRule, RuleBody, RuleTrigger

__all__ = [
    "MessageList",
    "NotificationRule",
    "RuleBody",
    "RuleTrigger",
    "dispatch_rules",
    "make_terminal_call_reminder",
]
