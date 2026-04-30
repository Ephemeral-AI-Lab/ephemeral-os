"""Soft reminder for partial plans below partial-planned ancestor graphs."""

from __future__ import annotations

from typing import Any

from notification.rules import NotificationRule
from task_center.exceptions import GraphInvariantViolation
from tools.core.context import ToolExecutionContextService
from tools.submission.context import (
    HarnessSubmissionContextError,
    resolve_harness_submission_context,
)
from tools.submission.hooks.recursive_partial_plan_gate import (
    request_has_partial_plan_ancestor,
)


def make_recursive_partial_plan_reminder() -> NotificationRule:
    def _trigger(messages: list[Any], context: Any) -> bool:
        del messages
        metadata = getattr(context, "tool_metadata", None)
        if metadata is None:
            return False
        try:
            submission_context = resolve_harness_submission_context(
                ToolExecutionContextService(
                    cwd=getattr(context, "cwd", "."),
                    services=metadata,
                )
            )
        except HarnessSubmissionContextError:
            return False
        try:
            return request_has_partial_plan_ancestor(
                submission_context.request,
                submission_context.runtime,
            )
        except GraphInvariantViolation:
            return False

    def _body(messages: list[Any], context: Any) -> str:
        del messages, context
        return (
            "submit_partial_plan is disabled because an ancestor complex-task "
            "request was spawned from a partial-planned harness graph. Use "
            "submit_full_plan for the current request."
        )

    return NotificationRule(
        name="recursive_partial_plan",
        trigger=_trigger,
        body=_body,
    )
