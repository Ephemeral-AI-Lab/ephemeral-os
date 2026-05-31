"""Launch reminder for generators in nested workflows."""

from __future__ import annotations

from typing import Any

from notification import NotificationRule
from tools.submission.notification_triggers._workflow_depth import (
    tool_context_is_nested_workflow,
)


def make_nested_workflow_handoff_disabled_reminder() -> NotificationRule:
    def _trigger(messages: list[Any], context: Any) -> bool:
        del messages
        return tool_context_is_nested_workflow(context)

    def _body(messages: list[Any], context: Any) -> str:
        del messages, context
        return (
            "Nested workflow generator reminder: submit_workflow_handoff is "
            "disabled here. Finish this generator task with "
            "submit_generator_outcome(status=\"success\", outcome=...) or "
            "submit_generator_outcome(status=\"failed\", outcome=...)."
        )

    return NotificationRule(
        name="nested_workflow_handoff_disabled",
        trigger=_trigger,
        body=_body,
    )


__all__ = ["make_nested_workflow_handoff_disabled_reminder"]
