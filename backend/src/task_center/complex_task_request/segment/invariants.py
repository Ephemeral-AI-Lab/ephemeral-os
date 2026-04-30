"""Segment-level invariants. All raise ``GraphInvariantViolation`` on breach."""

from __future__ import annotations

from task_center.domain.harness_graph import HarnessGraph, HarnessGraphStatus
from task_center.domain.task_segment import TaskSegment, TaskSegmentStatus
from task_center.exceptions import GraphInvariantViolation


def assert_segment_open(segment: TaskSegment) -> None:
    if not segment.is_open:
        raise GraphInvariantViolation(
            f"TaskSegment {segment.id!r} is not open (status={segment.status})"
        )


def assert_segment_open_for_graph_creation(segment: TaskSegment) -> None:
    assert_segment_open(segment)


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


# Re-export for callers that import segment-level invariants alongside
# segment-status checks.
__all__ = [
    "TaskSegmentStatus",  # convenience re-export
    "assert_continuation_goal_only_from_passing_graph",
    "assert_graph_belongs_to_segment",
    "assert_passing_graph_closes_segment",
    "assert_segment_has_budget",
    "assert_segment_open",
    "assert_segment_open_for_graph_creation",
]
