"""Tests for the public ``get_attempt_count`` helper."""

from __future__ import annotations

from datetime import UTC, datetime

from task_center.complex_task_request.segment.attempt_count import (
    get_attempt_count,
)
from task_center.domain.task_segment import (
    TaskSegment,
    TaskSegmentCreationReason,
    TaskSegmentStatus,
)


def _seg(harness_graph_ids: tuple[str, ...]) -> TaskSegment:
    now = datetime.now(UTC)
    return TaskSegment(
        id="s1",
        complex_task_request_id="r1",
        sequence_no=1,
        creation_reason=TaskSegmentCreationReason.INITIAL,
        goal="g",
        attempt_budget=2,
        status=TaskSegmentStatus.OPEN,
        harness_graph_ids=harness_graph_ids,
        continuation_goal=None,
        created_at=now,
        updated_at=now,
        closed_at=None,
    )


def test_get_attempt_count_derived_from_list():
    assert get_attempt_count(_seg(())) == 0
    assert get_attempt_count(_seg(("g1",))) == 1
    assert get_attempt_count(_seg(("g1", "g2"))) == 2
