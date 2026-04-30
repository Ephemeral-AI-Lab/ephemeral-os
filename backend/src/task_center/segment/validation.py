"""TaskSegment-layer invariants. All raise ``GraphInvariantViolation``."""

from __future__ import annotations

from task_center.exceptions import GraphInvariantViolation
from task_center.harness_graph.graph import HarnessGraph
from task_center.segment.segment import TaskSegment


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


def assert_graph_belongs_to_segment(
    graph: HarnessGraph, segment: TaskSegment
) -> None:
    if graph.task_segment_id != segment.id:
        raise GraphInvariantViolation(
            f"HarnessGraph {graph.id!r} (segment {graph.task_segment_id!r}) "
            f"does not belong to TaskSegment {segment.id!r}"
        )
