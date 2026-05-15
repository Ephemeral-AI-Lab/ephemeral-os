"""Invariant tests across request, iteration, and attempt levels."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from task_center._core.infra import (
    assert_continuation_iteration_predecessor,
    assert_goal_open,
    assert_iteration_id_unique_in_goal,
    assert_iteration_sequence_contiguous,
)
from task_center._core.infra import (
    assert_fail_reason_present_on_failure,
    assert_trial_sequence_contiguous,
)
from task_center._core.infra import (
    assert_trial_belongs_to_iteration,
    assert_iteration_has_budget,
    assert_iteration_open,
)
from task_center.iteration import IterationManagerRegistry
from task_center.goal.state import (
    Goal,
    GoalStatus,
)
from task_center.trial import (
    Trial,
    TrialFailReason,
    TrialStage,
    TrialStatus,
)
from task_center.iteration.state import (
    Iteration,
    IterationCreationReason,
    IterationStatus,
)
from task_center._core.types import TaskCenterInvariantViolation


def _request(
    status: GoalStatus = GoalStatus.OPEN,
    iteration_ids: tuple[str, ...] = (),
) -> Goal:
    now = datetime.now(UTC)
    return Goal(
        id="r1",
        task_center_run_id="run1",
        requested_by_task_id="t1",
        goal="g",
        status=status,
        iteration_ids=iteration_ids,
        final_outcome=None,
        created_at=now,
        updated_at=now,
        closed_at=None,
    )


def _segment(
    *,
    status: IterationStatus = IterationStatus.OPEN,
    trial_ids: tuple[str, ...] = (),
    continuation_goal: str | None = None,
    trial_budget: int = 2,
    sid: str = "s1",
) -> Iteration:
    now = datetime.now(UTC)
    return Iteration(
        id=sid,
        goal_id="r1",
        sequence_no=1,
        creation_reason=IterationCreationReason.INITIAL,
        goal="g",
        trial_budget=trial_budget,
        status=status,
        trial_ids=trial_ids,
        continuation_goal=continuation_goal,
        created_at=now,
        updated_at=now,
        closed_at=None,
    )


def _graph(
    *,
    status: TrialStatus = TrialStatus.RUNNING,
    fail_reason: TrialFailReason | None = None,
    iteration_id: str = "s1",
    gid: str = "g1",
) -> Trial:
    now = datetime.now(UTC)
    return Trial(
        id=gid,
        iteration_id=iteration_id,
        trial_sequence_no=1,
        stage=TrialStage.PLAN,
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


def test_assert_mission_open_passes_for_open():
    assert_goal_open(_request(status=GoalStatus.OPEN))


def test_assert_mission_open_fails_for_closed():
    for status in (
        GoalStatus.SUCCEEDED,
        GoalStatus.FAILED,
        GoalStatus.CANCELLED,
    ):
        with pytest.raises(TaskCenterInvariantViolation):
            assert_goal_open(_request(status=status))


def test_assert_episode_id_unique_in_mission():
    assert_iteration_id_unique_in_goal(
        _request(iteration_ids=("s1", "s2")), "s3"
    )
    with pytest.raises(TaskCenterInvariantViolation):
        assert_iteration_id_unique_in_goal(
            _request(iteration_ids=("s1",)), "s1"
        )


def test_assert_episode_sequence_contiguous():
    assert_iteration_sequence_contiguous(_request(iteration_ids=()), 1)
    assert_iteration_sequence_contiguous(_request(iteration_ids=("s1",)), 2)
    with pytest.raises(TaskCenterInvariantViolation):
        assert_iteration_sequence_contiguous(_request(iteration_ids=("s1",)), 1)
    with pytest.raises(TaskCenterInvariantViolation):
        assert_iteration_sequence_contiguous(_request(iteration_ids=("s1",)), 3)


def test_assert_continuation_episode_predecessor_requires_succeeded_with_goal():
    succeeded_with_goal = _segment(
        status=IterationStatus.SUCCEEDED, continuation_goal="next"
    )
    assert_continuation_iteration_predecessor(succeeded_with_goal)

    with pytest.raises(TaskCenterInvariantViolation):
        assert_continuation_iteration_predecessor(
            _segment(status=IterationStatus.OPEN, continuation_goal="next")
        )
    with pytest.raises(TaskCenterInvariantViolation):
        assert_continuation_iteration_predecessor(
            _segment(status=IterationStatus.SUCCEEDED, continuation_goal=None)
        )


# ---- Segment-level ------------------------------------------------------


def test_assert_episode_open():
    assert_iteration_open(_segment(status=IterationStatus.OPEN))
    with pytest.raises(TaskCenterInvariantViolation):
        assert_iteration_open(_segment(status=IterationStatus.SUCCEEDED))


def test_assert_episode_has_budget():
    assert_iteration_has_budget(_segment(trial_budget=2, trial_ids=()))
    assert_iteration_has_budget(
        _segment(trial_budget=2, trial_ids=("g1",))
    )
    with pytest.raises(TaskCenterInvariantViolation):
        assert_iteration_has_budget(
            _segment(trial_budget=2, trial_ids=("g1", "g2"))
        )


def test_assert_attempt_belongs_to_episode():
    assert_trial_belongs_to_iteration(
        _graph(iteration_id="s1"), _segment(sid="s1")
    )
    with pytest.raises(TaskCenterInvariantViolation):
        assert_trial_belongs_to_iteration(
            _graph(iteration_id="s1"), _segment(sid="s2")
        )


# ---- Graph-level --------------------------------------------------------


def test_assert_attempt_sequence_contiguous():
    assert_trial_sequence_contiguous(_segment(trial_ids=()), 1)
    assert_trial_sequence_contiguous(_segment(trial_ids=("g1",)), 2)
    with pytest.raises(TaskCenterInvariantViolation):
        assert_trial_sequence_contiguous(_segment(trial_ids=("g1",)), 1)


def test_assert_fail_reason_present_on_failure():
    assert_fail_reason_present_on_failure(
        _graph(status=TrialStatus.PASSED)
    )
    assert_fail_reason_present_on_failure(
        _graph(
            status=TrialStatus.FAILED,
            fail_reason=TrialFailReason.GENERATOR_FAILED,
        )
    )
    with pytest.raises(TaskCenterInvariantViolation):
        assert_fail_reason_present_on_failure(
            _graph(status=TrialStatus.FAILED, fail_reason=None)
        )


# ---- Manager registry ---------------------------------------------------


def test_episode_manager_registry_enforces_uniqueness():
    reg = IterationManagerRegistry()

    class _Fake:
        iteration_id = "s1"

    reg.register(_Fake())  # type: ignore[arg-type]
    assert reg.get("s1") is not None
    with pytest.raises(TaskCenterInvariantViolation):
        reg.register(_Fake())  # type: ignore[arg-type]
    reg.deregister("s1")
    assert reg.get("s1") is None
