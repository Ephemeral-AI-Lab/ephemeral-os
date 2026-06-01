"""Domain DTO tests for Attempt."""

from __future__ import annotations

from datetime import UTC, datetime

from workflow.attempt import (
    Attempt,
    AttemptFailReason,
    AttemptStage,
    AttemptStatus,
)


def _graph(**overrides) -> Attempt:
    base = dict(
        id="g1",
        iteration_id="s1",
        attempt_sequence_no=1,
        stage=AttemptStage.PLAN,
        status=AttemptStatus.RUNNING,
        planner_task_id=None,
        generator_task_ids=(),
        reducer_task_ids=(),
        deferred_goal_for_next_iteration=None,
        fail_reason=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        closed_at=None,
    )
    base.update(overrides)
    return Attempt(**base)


def test_is_closed_matches_stage():
    assert _graph(stage=AttemptStage.PLAN).is_closed is False
    assert _graph(stage=AttemptStage.RUN).is_closed is False
    assert _graph(stage=AttemptStage.CLOSED).is_closed is True


def test_fail_reason_enum_values():
    assert AttemptFailReason.TASK_FAILED.value == "task_failed"
    assert AttemptFailReason.STARTUP_FAILED.value == "startup_failed"
