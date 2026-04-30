"""HarnessGraph-layer invariants. All raise ``GraphInvariantViolation``."""

from __future__ import annotations

from task_center.exceptions import GraphInvariantViolation
from task_center.harness_graph.graph import HarnessGraph, HarnessGraphStatus
from task_center.segment.segment import TaskSegment


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
