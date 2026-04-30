"""Prehook blocking partial plans below partial-planned ancestor graphs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from task_center.complex_task.request import ComplexTaskRequest
from task_center.exceptions import GraphInvariantViolation
from task_center.harness_graph.runtime import HarnessGraphRuntime
from tools.core.context import ToolExecutionContextService
from tools.core.hooks import HookResult
from tools.submission.context import (
    HarnessSubmissionContextError,
    resolve_harness_submission_context,
)


def request_has_partial_plan_ancestor(
    request: ComplexTaskRequest,
    runtime: HarnessGraphRuntime,
) -> bool:
    seen_request_ids: set[str] = set()
    current_request = request

    while True:
        if current_request.id in seen_request_ids:
            raise GraphInvariantViolation(
                "Cycle detected while resolving complex task request ancestry."
            )
        seen_request_ids.add(current_request.id)

        parent_task = runtime.task_store.get_task(
            current_request.requested_by_task_id
        )
        if parent_task is None:
            return False

        parent_graph_id = str(parent_task.get("task_center_harness_graph_id") or "")
        if not parent_graph_id:
            return False

        parent_graph = runtime.graph_store.get(parent_graph_id)
        if parent_graph is None:
            raise GraphInvariantViolation(
                f"Parent HarnessGraph {parent_graph_id!r} was not found."
            )

        if parent_graph.continuation_goal is not None:
            return True

        parent_segment = runtime.segment_store.get(parent_graph.task_segment_id)
        if parent_segment is None:
            raise GraphInvariantViolation(
                f"Parent TaskSegment {parent_graph.task_segment_id!r} was not found."
            )

        parent_request = runtime.request_store.get(
            parent_segment.complex_task_request_id
        )
        if parent_request is None:
            raise GraphInvariantViolation(
                "Parent ComplexTaskRequest "
                f"{parent_segment.complex_task_request_id!r} was not found."
            )

        current_request = parent_request


@dataclass(frozen=True, slots=True)
class PartialPlanAncestorGate:
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

        try:
            has_partial_ancestor = request_has_partial_plan_ancestor(
                submission_context.request,
                submission_context.runtime,
            )
        except GraphInvariantViolation as exc:
            return HookResult.fail(str(exc))

        if has_partial_ancestor:
            return HookResult.fail(
                "submit_partial_plan is disabled for this request because an "
                "ancestor complex-task request was spawned from a partial-planned "
                "harness graph. Submit a full plan for the current request."
            )
        return HookResult.pass_(tool_input)
