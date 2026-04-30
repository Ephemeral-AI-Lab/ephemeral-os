"""Invariant tests across request, segment, and graph levels."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from task_center.complex_task_request.invariants import (
    assert_continuation_segment_predecessor,
    assert_no_root_creation_reason,
    assert_request_open,
    assert_segment_id_unique_in_list,
    assert_segment_sequence_contiguous,
)
from task_center.complex_task_request.segment.harness_graph.invariants import (
    assert_fail_reason_present_on_failure,
    assert_graph_running,
    assert_graph_sequence_contiguous,
)
from task_center.complex_task_request.segment.invariants import (
    assert_continuation_goal_only_from_passing_graph,
    assert_graph_belongs_to_segment,
    assert_passing_graph_closes_segment,
    assert_segment_has_budget,
    assert_segment_open,
)
from task_center.complex_task_request.segment_manager_registry import (
    SegmentManagerRegistry,
)
from task_center.domain.complex_task_request import (
    ComplexTaskRequest,
    ComplexTaskRequestStatus,
)
from task_center.domain.harness_graph import (
    HarnessGraph,
    HarnessGraphFailReason,
    HarnessGraphStage,
    HarnessGraphStatus,
)
from task_center.domain.task_segment import (
    TaskSegment,
    TaskSegmentCreationReason,
    TaskSegmentStatus,
)
from task_center.exceptions import GraphInvariantViolation


def _request(
    status: ComplexTaskRequestStatus = ComplexTaskRequestStatus.OPEN,
    task_segment_ids: tuple[str, ...] = (),
) -> ComplexTaskRequest:
    now = datetime.now(UTC)
    return ComplexTaskRequest(
        id="r1",
        task_center_run_id="run1",
        requested_by_task_id="t1",
        goal="g",
        status=status,
        task_segment_ids=task_segment_ids,
        final_outcome=None,
        created_at=now,
        updated_at=now,
        closed_at=None,
    )


def _segment(
    *,
    status: TaskSegmentStatus = TaskSegmentStatus.OPEN,
    harness_graph_ids: tuple[str, ...] = (),
    continuation_goal: str | None = None,
    attempt_budget: int = 2,
    sid: str = "s1",
) -> TaskSegment:
    now = datetime.now(UTC)
    return TaskSegment(
        id=sid,
        complex_task_request_id="r1",
        sequence_no=1,
        creation_reason=TaskSegmentCreationReason.INITIAL,
        goal="g",
        attempt_budget=attempt_budget,
        status=status,
        harness_graph_ids=harness_graph_ids,
        continuation_goal=continuation_goal,
        created_at=now,
        updated_at=now,
        closed_at=None,
    )


def _graph(
    *,
    status: HarnessGraphStatus = HarnessGraphStatus.RUNNING,
    fail_reason: HarnessGraphFailReason | None = None,
    task_segment_id: str = "s1",
    gid: str = "g1",
) -> HarnessGraph:
    now = datetime.now(UTC)
    return HarnessGraph(
        id=gid,
        task_segment_id=task_segment_id,
        graph_sequence_no=1,
        stage=HarnessGraphStage.PLANNING,
        status=status,
        planner_task_id=None,
        task_specification=None,
        evaluation_criteria=(),
        generator_task_ids=(),
        evaluator_task_id=None,
        continuation_goal=None,
        fail_reason=fail_reason,
        created_at=now,
        updated_at=now,
        closed_at=None,
    )


# ---- Request-level ------------------------------------------------------


def test_assert_request_open_passes_for_open():
    assert_request_open(_request(status=ComplexTaskRequestStatus.OPEN))


def test_assert_request_open_fails_for_closed():
    for status in (
        ComplexTaskRequestStatus.SUCCEEDED,
        ComplexTaskRequestStatus.FAILED,
        ComplexTaskRequestStatus.CANCELLED,
    ):
        with pytest.raises(GraphInvariantViolation):
            assert_request_open(_request(status=status))


def test_assert_segment_id_unique_in_list():
    assert_segment_id_unique_in_list(
        _request(task_segment_ids=("s1", "s2")), "s3"
    )
    with pytest.raises(GraphInvariantViolation):
        assert_segment_id_unique_in_list(
            _request(task_segment_ids=("s1",)), "s1"
        )


def test_assert_segment_sequence_contiguous():
    assert_segment_sequence_contiguous(_request(task_segment_ids=()), 1)
    assert_segment_sequence_contiguous(_request(task_segment_ids=("s1",)), 2)
    with pytest.raises(GraphInvariantViolation):
        assert_segment_sequence_contiguous(_request(task_segment_ids=("s1",)), 1)
    with pytest.raises(GraphInvariantViolation):
        assert_segment_sequence_contiguous(_request(task_segment_ids=("s1",)), 3)


def test_assert_no_root_creation_reason_passes_known_kinds():
    assert_no_root_creation_reason(TaskSegmentCreationReason.INITIAL.value)
    assert_no_root_creation_reason(
        TaskSegmentCreationReason.PARTIAL_CONTINUATION.value
    )


def test_assert_no_root_creation_reason_rejects_root():
    with pytest.raises(GraphInvariantViolation):
        assert_no_root_creation_reason("root")


def test_assert_continuation_segment_predecessor_requires_succeeded_with_goal():
    succeeded_with_goal = _segment(
        status=TaskSegmentStatus.SUCCEEDED, continuation_goal="next"
    )
    assert_continuation_segment_predecessor(succeeded_with_goal)

    with pytest.raises(GraphInvariantViolation):
        assert_continuation_segment_predecessor(
            _segment(status=TaskSegmentStatus.OPEN, continuation_goal="next")
        )
    with pytest.raises(GraphInvariantViolation):
        assert_continuation_segment_predecessor(
            _segment(status=TaskSegmentStatus.SUCCEEDED, continuation_goal=None)
        )


# ---- Segment-level ------------------------------------------------------


def test_assert_segment_open():
    assert_segment_open(_segment(status=TaskSegmentStatus.OPEN))
    with pytest.raises(GraphInvariantViolation):
        assert_segment_open(_segment(status=TaskSegmentStatus.SUCCEEDED))


def test_assert_segment_has_budget():
    assert_segment_has_budget(_segment(attempt_budget=2, harness_graph_ids=()))
    assert_segment_has_budget(
        _segment(attempt_budget=2, harness_graph_ids=("g1",))
    )
    with pytest.raises(GraphInvariantViolation):
        assert_segment_has_budget(
            _segment(attempt_budget=2, harness_graph_ids=("g1", "g2"))
        )


def test_assert_passing_graph_closes_segment():
    assert_passing_graph_closes_segment(_graph(status=HarnessGraphStatus.PASSED))
    with pytest.raises(GraphInvariantViolation):
        assert_passing_graph_closes_segment(
            _graph(status=HarnessGraphStatus.RUNNING)
        )


def test_assert_continuation_goal_only_from_passing_graph():
    assert_continuation_goal_only_from_passing_graph(
        _graph(status=HarnessGraphStatus.PASSED),
        _segment(continuation_goal="next"),
    )
    with pytest.raises(GraphInvariantViolation):
        assert_continuation_goal_only_from_passing_graph(
            _graph(
                status=HarnessGraphStatus.FAILED,
                fail_reason=HarnessGraphFailReason.GENERATOR_FAILED,
            ),
            _segment(continuation_goal="next"),
        )


def test_assert_graph_belongs_to_segment():
    assert_graph_belongs_to_segment(
        _graph(task_segment_id="s1"), _segment(sid="s1")
    )
    with pytest.raises(GraphInvariantViolation):
        assert_graph_belongs_to_segment(
            _graph(task_segment_id="s1"), _segment(sid="s2")
        )


# ---- Graph-level --------------------------------------------------------


def test_assert_graph_running():
    assert_graph_running(_graph(status=HarnessGraphStatus.RUNNING))
    with pytest.raises(GraphInvariantViolation):
        assert_graph_running(_graph(status=HarnessGraphStatus.PASSED))


def test_assert_graph_sequence_contiguous():
    assert_graph_sequence_contiguous(_segment(harness_graph_ids=()), 1)
    assert_graph_sequence_contiguous(_segment(harness_graph_ids=("g1",)), 2)
    with pytest.raises(GraphInvariantViolation):
        assert_graph_sequence_contiguous(_segment(harness_graph_ids=("g1",)), 1)


def test_assert_fail_reason_present_on_failure():
    assert_fail_reason_present_on_failure(
        _graph(status=HarnessGraphStatus.PASSED)
    )
    assert_fail_reason_present_on_failure(
        _graph(
            status=HarnessGraphStatus.FAILED,
            fail_reason=HarnessGraphFailReason.GENERATOR_FAILED,
        )
    )
    with pytest.raises(GraphInvariantViolation):
        assert_fail_reason_present_on_failure(
            _graph(status=HarnessGraphStatus.FAILED, fail_reason=None)
        )


# ---- Manager registry ---------------------------------------------------


def test_segment_manager_registry_enforces_uniqueness():
    reg = SegmentManagerRegistry()

    class _Fake:
        task_segment_id = "s1"

    reg.register(_Fake())  # type: ignore[arg-type]
    assert reg.get("s1") is not None
    with pytest.raises(GraphInvariantViolation):
        reg.register(_Fake())  # type: ignore[arg-type]
    reg.deregister("s1")
    assert reg.get("s1") is None
