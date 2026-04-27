"""Library of notification rule factories.

Per-agent `definition.py` files import factories from here and assemble
them into `AgentDefinition.notification_rules`.
"""

from notification.library.budget_warning import make_budget_warning
from notification.library.opening_reminder import make_opening_reminder

__all__ = [
    "make_budget_warning",
    "make_opening_reminder",
]
