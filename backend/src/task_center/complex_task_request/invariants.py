"""Request-level invariants. All raise ``GraphInvariantViolation`` on breach."""

from __future__ import annotations

from task_center.domain.complex_task_request import ComplexTaskRequest
from task_center.domain.task_segment import TaskSegment, TaskSegmentStatus
from task_center.exceptions import GraphInvariantViolation


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
