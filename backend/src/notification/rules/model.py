"""Notification rule model.

Rules are the single source of truth for engine-generated `<system-reminder>`
content. Each rule's `trigger` is evaluated by `dispatch_rules`; when it
returns True, `body` produces the reminder text.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from message.message import Message


MessageList = list[Message]

# Note: ``body`` and ``trigger`` receive ``(messages, QueryContext)`` at
# runtime. The type is loosened to ``Callable[..., ...]`` so that Pydantic
# (which validates ``AgentDefinition.notification_rules: list[NotificationRule]``)
# does not try to resolve the forward reference to ``QueryContext`` and
# raise ``PydanticUserError: not fully defined``.
RuleBody = Callable[..., str]
RuleTrigger = Callable[..., bool]


@dataclass(frozen=True)
class NotificationRule:
    """Declarative rule for emitting a `<system-reminder>` block.

    `trigger` and `body` both receive `(messages, context)` so rules can
    inspect the live transcript, agent identity, tool budget, and per-rule
    scratchpad without a separate context wrapper.

    `fire_once=True` (the default) skips the rule once its `name` is in the
    run's `notification_fired` set. Rules that need to fire repeatedly
    (e.g., budget warnings at multiple thresholds) set `fire_once=False`
    and manage their own dedup via `context.notification_state[name]`.
    """

    name: str
    body: RuleBody
    trigger: RuleTrigger
    fire_once: bool = True
