"""Domain DTO tests for Trial."""

from __future__ import annotations

from datetime import UTC, datetime

from task_center.trial import (
    Trial,
    TrialFailReason,
    TrialStage,
    TrialStatus,
)


def _graph(**overrides) -> Trial:
    base = dict(
        id="g1",
        episode_id="s1",
        trial_sequence_no=1,
        stage=TrialStage.PLAN,
        status=TrialStatus.RUNNING,
        planner_task_id=None,
        task_specification=None,
        evaluation_criteria=(),
        generator_task_ids=(),
        evaluator_task_id=None,
        continuation_goal=None,
        fail_reason=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        closed_at=None,
    )
    base.update(overrides)
    return Trial(**base)


def test_has_partial_continuation_matches_continuation_goal():
    assert _graph(continuation_goal=None).has_partial_continuation is False
    assert _graph(continuation_goal="x").has_partial_continuation is True


def test_is_closed_matches_stage():
    assert _graph(stage=TrialStage.PLAN).is_closed is False
    assert _graph(stage=TrialStage.GENERATE).is_closed is False
    assert _graph(stage=TrialStage.EVALUATE).is_closed is False
    assert _graph(stage=TrialStage.CLOSED).is_closed is True


def test_fail_reason_enum_values():
    assert (
        TrialFailReason.PLANNER_FAILED.value
        == "planner_failed"
    )
    assert TrialFailReason.GENERATOR_FAILED.value == "generator_failed"
    assert TrialFailReason.EVALUATOR_FAILED.value == "evaluator_failed"
    assert TrialFailReason.STARTUP_FAILED.value == "startup_failed"
