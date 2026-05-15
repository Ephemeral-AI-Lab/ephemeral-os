"""Domain DTO tests for Iteration."""

from __future__ import annotations

from datetime import UTC, datetime

from task_center.iteration.state import (
    Iteration,
    IterationCreationReason,
    IterationStatus,
)


def _seg(**overrides) -> Iteration:
    base = dict(
        id="s1",
        goal_id="r1",
        sequence_no=1,
        creation_reason=IterationCreationReason.INITIAL,
        goal="g",
        trial_budget=2,
        status=IterationStatus.OPEN,
        trial_ids=(),
        continuation_goal=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        closed_at=None,
    )
    base.update(overrides)
    return Iteration(**base)


def test_attempt_count_equals_len_of_attempt_ids():
    assert _seg(trial_ids=()).trial_count == 0
    assert _seg(trial_ids=("g1",)).trial_count == 1
    assert _seg(trial_ids=("g1", "g2")).trial_count == 2


def test_has_budget_remaining_flips_at_boundary():
    assert _seg(trial_budget=2, trial_ids=()).has_budget_remaining
    assert _seg(
        trial_budget=2, trial_ids=("g1",)
    ).has_budget_remaining
    assert not _seg(
        trial_budget=2, trial_ids=("g1", "g2")
    ).has_budget_remaining


def test_latest_attempt_id_returns_last():
    assert _seg().latest_trial_id is None
    assert _seg(trial_ids=("a", "b")).latest_trial_id == "b"


def test_is_open_matches_status():
    assert _seg(status=IterationStatus.OPEN).is_open
    assert not _seg(status=IterationStatus.SUCCEEDED).is_open
    assert not _seg(status=IterationStatus.FAILED).is_open
