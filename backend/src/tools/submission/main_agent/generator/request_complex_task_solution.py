"""request_complex_task_solution orchestration handoff tool."""

from __future__ import annotations

from pydantic import BaseModel, Field

from task_center.complex_task.handler import ComplexTaskRequestHandler
from task_center.complex_task.request import ComplexTaskCloseReport
from task_center.exceptions import GraphInvariantViolation
from task_center.harness_graph.runtime import HarnessGraphRuntime
from task_center.task import HarnessTaskRole, HarnessTaskStatus
from tools.core.context import ToolExecutionContextService
from tools.core.decorator import tool
from tools.core.results import TextToolOutput, ToolResult
from tools.submission.context import (
    HarnessSubmissionContextError,
    resolve_harness_submission_context,
)
from tools.submission.hooks import (
    HarnessRoleGate,
    RequestComplexTaskBeforeEditGate,
)


class RequestComplexTaskSolutionInput(BaseModel):
    goal: str = Field(..., min_length=1)


def _deliver_complex_task_close_report(
    runtime: HarnessGraphRuntime,
    report: ComplexTaskCloseReport,
) -> None:
    task = runtime.task_store.get_task(report.requested_by_task_id)
    if task is None:
        raise GraphInvariantViolation(
            f"TaskCenter task {report.requested_by_task_id!r} was not found."
        )
    graph_id = str(task.get("task_center_harness_graph_id") or "").strip()
    if not graph_id:
        raise GraphInvariantViolation(
            f"TaskCenter task {report.requested_by_task_id!r} is not attached "
            "to a harness graph."
        )
    orchestrator = runtime.orchestrator_registry.get_or_raise(graph_id)
    orchestrator.apply_complex_task_close_report(report)


def _make_complex_task_request_handler(
    runtime: HarnessGraphRuntime,
) -> ComplexTaskRequestHandler:
    if runtime.manager_registry is None:
        raise GraphInvariantViolation(
            "request_complex_task_solution requires a segment manager registry."
        )

    from task_center.harness_graph.factory import (
        make_harness_graph_orchestrator_factory,
    )

    return ComplexTaskRequestHandler(
        request_store=runtime.request_store,
        segment_store=runtime.segment_store,
        graph_store=runtime.graph_store,
        manager_registry=runtime.manager_registry,
        config=runtime.lifecycle_config,
        deliver_close_report=lambda report: _deliver_complex_task_close_report(
            runtime,
            report,
        ),
        orchestrator_factory=make_harness_graph_orchestrator_factory(
            graph_store=runtime.graph_store,
            runtime=runtime,
        ),
    )


@tool(
    name="request_complex_task_solution",
    description=(
        "Request a delegated complex-task solution for the current generator task. "
        "This must be called before making edits."
    ),
    input_model=RequestComplexTaskSolutionInput,
    output_model=TextToolOutput,
    is_terminal_tool=True,
    pre_hooks=(
        HarnessRoleGate("request_complex_task_solution", HarnessTaskRole.GENERATOR),
        RequestComplexTaskBeforeEditGate(),
    ),
)
async def request_complex_task_solution(
    goal: str,
    *,
    context: ToolExecutionContextService,
) -> ToolResult:
    clean_goal = goal.strip()
    if not clean_goal:
        return ToolResult(
            output="request_complex_task_solution requires a nonblank goal.",
            is_error=True,
        )

    try:
        submission_context = resolve_harness_submission_context(context)
    except HarnessSubmissionContextError as exc:
        return ToolResult(output=str(exc), is_error=True)

    if submission_context.task.get("status") != HarnessTaskStatus.RUNNING.value:
        return ToolResult(
            output=(
                "request_complex_task_solution is only valid for a running "
                "generator task."
            ),
            is_error=True,
        )

    try:
        request_handler = _make_complex_task_request_handler(submission_context.runtime)
        delegated_request = request_handler.create_complex_task_request(
            task_center_run_id=submission_context.task["task_center_run_id"],
            requested_by_task_id=submission_context.task_center_task_id,
            goal=clean_goal,
        )
        initial_segment = request_handler.create_initial_segment(
            complex_task_request_id=delegated_request.id,
        )
        manager = submission_context.runtime.manager_registry
        if manager is None:
            raise GraphInvariantViolation(
                "request_complex_task_solution requires a segment manager registry."
            )
        segment_manager = manager.get(initial_segment.id)
        if segment_manager is None:
            raise GraphInvariantViolation(
                f"TaskSegmentManager {initial_segment.id!r} was not registered."
            )
        submission_context.runtime.task_store.set_task_status(
            submission_context.task_center_task_id,
            status=HarnessTaskStatus.WAITING_COMPLEX_TASK.value,
            summary={
                "outcome": "complex_task_handoff",
                "summary": "Waiting on delegated complex task solution.",
                "payload": {
                    "complex_task_request_id": delegated_request.id,
                    "initial_segment_id": initial_segment.id,
                    "goal": clean_goal,
                },
            },
        )
        created_harness_graph = segment_manager.create_initial_harness_graph()
    except GraphInvariantViolation as exc:
        return ToolResult(output=str(exc), is_error=True)

    return ToolResult(
        output=(
            "Started delegated complex task request "
            f"{delegated_request.id} for this generator task."
        ),
        metadata={
            "submission_kind": "complex_task_handoff",
            "task_center_task_id": submission_context.task_center_task_id,
            "harness_graph_id": submission_context.graph.id,
            "complex_task_request_id": delegated_request.id,
            "initial_segment_id": initial_segment.id,
            "initial_harness_graph_id": created_harness_graph.id,
        },
    )
