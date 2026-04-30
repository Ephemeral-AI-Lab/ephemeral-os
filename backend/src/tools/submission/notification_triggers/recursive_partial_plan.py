"""Soft reminder for recursive partial-plan blocking."""

from __future__ import annotations

from typing import Any

from notification.rules import NotificationRule
from tools.core.context import ToolExecutionContextService
from tools.submission.context import (
    HarnessSubmissionContextError,
    resolve_harness_submission_context,
)


def _has_prior_partial_continuation(submission_context: Any) -> bool:
    request = submission_context.request
    current_segment = submission_context.segment
    segment_store = submission_context.runtime.segment_store
    for segment_id in request.task_segment_ids:
        if segment_id == current_segment.id:
            return False
        segment = segment_store.get(segment_id)
        if segment is not None and segment.continuation_goal is not None:
            return True
    return False


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
        return _has_prior_partial_continuation(submission_context)

    def _body(messages: list[Any], context: Any) -> str:
        del messages, context
        return (
            "submit_partial_plan is disabled because a prior segment already "
            "used partial continuation. Use submit_full_plan for the current "
            "segment."
        )

    return NotificationRule(
        name="recursive_partial_plan",
        trigger=_trigger,
        body=_body,
    )
