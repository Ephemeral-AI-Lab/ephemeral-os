"""TaskCenter harness lifecycle invariants. All raise ``GraphInvariantViolation``."""

from __future__ import annotations

from task_center.domain.complex_task_request import ComplexTaskRequest
from task_center.domain.harness_graph import HarnessGraph, HarnessGraphStatus
from task_center.domain.task_segment import TaskSegment, TaskSegmentStatus
from task_center.exceptions import GraphInvariantViolation


# ---- request ---------------------------------------------------------------

def assert_request_open(request: ComplexTaskRequest) -> None:
    if not request.is_open:
        raise GraphInvariantViolation(
            f"ComplexTaskRequest {request.id!r} is not open (status={request.status})"
        )


def assert_segment_id_unique_in_list(
    request: ComplexTaskRequest, segment_id: str
) -> None:
    if segment_id in request.task_segment_ids:
        raise GraphInvariantViolation(
            f"TaskSegment {segment_id!r} already present in request "
            f"{request.id!r} segment list"
        )


def assert_segment_sequence_contiguous(
    request: ComplexTaskRequest, new_sequence_no: int
) -> None:
    expected = len(request.task_segment_ids) + 1
    if new_sequence_no != expected:
        raise GraphInvariantViolation(
            f"TaskSegment sequence_no must be contiguous: expected {expected}, "
            f"got {new_sequence_no}"
        )


def assert_no_root_creation_reason(creation_reason: str) -> None:
    if creation_reason == "root":
        raise GraphInvariantViolation(
            "Creation reason 'root' is not allowed; use 'initial' or 'partial_continuation'"
        )


def assert_continuation_segment_predecessor(previous: TaskSegment) -> None:
    if previous.status != TaskSegmentStatus.SUCCEEDED:
        raise GraphInvariantViolation(
            f"Continuation requires predecessor segment {previous.id!r} to be "
            f"SUCCEEDED, not {previous.status}"
        )
    if previous.continuation_goal is None:
        raise GraphInvariantViolation(
            f"Continuation requires predecessor segment {previous.id!r} to have a "
            f"continuation_goal; none was recorded"
        )


# ---- segment ---------------------------------------------------------------

def assert_segment_open(segment: TaskSegment) -> None:
    if not segment.is_open:
        raise GraphInvariantViolation(
            f"TaskSegment {segment.id!r} is not open (status={segment.status})"
        )


def assert_segment_has_budget(segment: TaskSegment) -> None:
    if not segment.has_budget_remaining:
        raise GraphInvariantViolation(
            f"TaskSegment {segment.id!r} attempt budget exhausted "
            f"({segment.attempt_count}/{segment.attempt_budget})"
        )


def assert_passing_graph_closes_segment(graph: HarnessGraph) -> None:
    if graph.status != HarnessGraphStatus.PASSED:
        raise GraphInvariantViolation(
            f"Expected passing HarnessGraph {graph.id!r}, got status={graph.status}"
        )


def assert_continuation_goal_only_from_passing_graph(
    graph: HarnessGraph, segment: TaskSegment
) -> None:
    if (
        segment.continuation_goal is not None
        and graph.status != HarnessGraphStatus.PASSED
    ):
        raise GraphInvariantViolation(
            f"TaskSegment {segment.id!r} continuation_goal must come from a "
            f"passing graph; HarnessGraph {graph.id!r} status={graph.status}"
        )


def assert_graph_belongs_to_segment(
    graph: HarnessGraph, segment: TaskSegment
) -> None:
    if graph.task_segment_id != segment.id:
        raise GraphInvariantViolation(
            f"HarnessGraph {graph.id!r} (segment {graph.task_segment_id!r}) "
            f"does not belong to TaskSegment {segment.id!r}"
        )


# ---- graph -----------------------------------------------------------------

def assert_graph_sequence_contiguous(
    segment: TaskSegment, new_sequence_no: int
) -> None:
    expected = len(segment.harness_graph_ids) + 1
    if new_sequence_no != expected:
        raise GraphInvariantViolation(
            f"HarnessGraph graph_sequence_no must be contiguous: expected "
            f"{expected}, got {new_sequence_no}"
        )


def assert_fail_reason_present_on_failure(graph: HarnessGraph) -> None:
    if graph.status == HarnessGraphStatus.FAILED and graph.fail_reason is None:
        raise GraphInvariantViolation(
            f"HarnessGraph {graph.id!r} closed FAILED with no fail_reason"
        )
