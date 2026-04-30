"""TaskCenter harness submission context resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from task_center.complex_task.request import ComplexTaskRequest
from task_center.exceptions import GraphInvariantViolation
from task_center.harness_graph.graph import HarnessGraph
from task_center.harness_graph.orchestrator import HarnessGraphOrchestrator
from task_center.harness_graph.runtime import HarnessGraphRuntime
from task_center.segment.segment import TaskSegment
from tools.core.context import ToolExecutionContextService


class HarnessSubmissionContextError(RuntimeError):
    """User-facing submission context resolution failure."""


@dataclass(frozen=True, slots=True)
class HarnessSubmissionContext:
    task_center_task_id: str
    task: dict[str, Any]
    graph: HarnessGraph
    segment: TaskSegment
    request: ComplexTaskRequest
    runtime: HarnessGraphRuntime
    orchestrator: HarnessGraphOrchestrator


def resolve_harness_submission_context(
    context: ToolExecutionContextService,
) -> HarnessSubmissionContext:
    """Resolve the current TaskCenter task into durable harness graph context."""
    runtime = context.get("harness_graph_runtime")
    if not isinstance(runtime, HarnessGraphRuntime):
        raise HarnessSubmissionContextError(
            "Missing harness graph runtime for this TaskCenter submission."
        )

    task_id = str(context.get("task_center_task_id") or "")
    if not task_id or task_id.isspace():
        raise HarnessSubmissionContextError(
            "Missing TaskCenter task id for this submission."
        )

    task = runtime.task_store.get_task(task_id)
    if task is None:
        raise HarnessSubmissionContextError(
            f"TaskCenter task {task_id!r} was not found."
        )

    graph_id = str(task.get("task_center_harness_graph_id") or "")
    if not graph_id or graph_id.isspace():
        raise HarnessSubmissionContextError(
            f"TaskCenter task {task_id!r} is not attached to a harness graph."
        )

    metadata_graph_id = str(context.get("task_center_harness_graph_id") or "")
    if metadata_graph_id.isspace():
        raise HarnessSubmissionContextError(
            "TaskCenter graph metadata is blank."
        )
    if metadata_graph_id and metadata_graph_id != graph_id:
        raise HarnessSubmissionContextError(
            "TaskCenter graph metadata does not match the persisted task row."
        )

    graph = runtime.graph_store.get(graph_id)
    if graph is None:
        raise HarnessSubmissionContextError(
            f"HarnessGraph {graph_id!r} was not found."
        )

    segment = runtime.segment_store.get(graph.task_segment_id)
    if segment is None:
        raise HarnessSubmissionContextError(
            f"TaskSegment {graph.task_segment_id!r} was not found."
        )

    request = runtime.request_store.get(segment.complex_task_request_id)
    if request is None:
        raise HarnessSubmissionContextError(
            f"ComplexTaskRequest {segment.complex_task_request_id!r} was not found."
        )

    try:
        orchestrator = runtime.orchestrator_registry.get_or_raise(graph_id)
    except GraphInvariantViolation as exc:
        raise HarnessSubmissionContextError(str(exc)) from exc

    return HarnessSubmissionContext(
        task_center_task_id=task_id,
        task=task,
        graph=graph,
        segment=segment,
        request=request,
        runtime=runtime,
        orchestrator=orchestrator,
    )
