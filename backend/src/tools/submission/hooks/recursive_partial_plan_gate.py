"""Prehook blocking recursive partial planner submissions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from task_center.complex_task.request import ComplexTaskRequest
from task_center.segment.segment import TaskSegment
from tools.core.context import ToolExecutionContextService
from tools.core.hooks import HookResult
from tools.submission.context import (
    HarnessSubmissionContextError,
    resolve_harness_submission_context,
)


def request_has_prior_partial_continuation(
    request: ComplexTaskRequest,
    current_segment: TaskSegment,
    segment_store: Any,
) -> bool:
    for segment_id in request.task_segment_ids:
        if segment_id == current_segment.id:
            return False
        segment = segment_store.get(segment_id)
        if segment is not None and segment.continuation_goal is not None:
            return True
    return False


@dataclass(frozen=True, slots=True)
class RecursivePartialPlanGate:
    target_tool: str = "submit_partial_plan"

    async def run(
        self,
        tool_input: BaseModel,
        context: ToolExecutionContextService,
    ) -> HookResult[Any]:
        try:
            submission_context = resolve_harness_submission_context(context)
        except HarnessSubmissionContextError as exc:
            return HookResult.fail(str(exc))

        if request_has_prior_partial_continuation(
            submission_context.request,
            submission_context.segment,
            submission_context.runtime.segment_store,
        ):
            return HookResult.fail(
                "submit_partial_plan is disabled for this request because a prior "
                "segment already used partial continuation. Submit a full plan "
                "for the current segment."
            )
        return HookResult.pass_(tool_input)
