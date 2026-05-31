"""Launch reminder for planners in nested workflows."""

from __future__ import annotations

from typing import Any

from notification import NotificationRule
from tools.submission.notification_triggers._workflow_depth import (
    tool_context_is_nested_workflow,
)


def make_nested_planner_deferral_disabled_reminder() -> NotificationRule:
    def _trigger(messages: list[Any], context: Any) -> bool:
        del messages
        return tool_context_is_nested_workflow(context)

    def _body(messages: list[Any], context: Any) -> str:
        del messages, context
        return (
            "Nested workflow planner reminder: use submit_planner_outcome, but omit "
            "deferred_goal_for_next_iteration. Your plan must cover all "
            "current child-workflow goal items and leave no remaining items."
        )

    return NotificationRule(
        name="nested_planner_deferral_disabled",
        trigger=_trigger,
        body=_body,
    )


__all__ = ["make_nested_planner_deferral_disabled_reminder"]
