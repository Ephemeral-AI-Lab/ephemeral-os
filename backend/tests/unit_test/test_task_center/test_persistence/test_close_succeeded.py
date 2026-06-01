"""US-009: IterationStore.close_succeeded atomicity + denormalization."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from workflow._core.state import (
    IterationCreationReason,
    IterationStatus,
)


def _seed_segment(workflow_store, iteration_store, task_center_run_id):
    req = workflow_store.insert(
        task_center_run_id=task_center_run_id,
        parent_task_id="parent-task",
        workflow_goal="g",
    )
    return iteration_store.insert(
        workflow_id=req.id,
        sequence_no=1,
        creation_reason=IterationCreationReason.INITIAL,
        iteration_goal="g",
        attempt_budget=2,
    )


def test_close_succeeded_writes_status_and_outcomes_atomically(
    workflow_store, iteration_store, task_center_run_id
):
    seg = _seed_segment(workflow_store, iteration_store, task_center_run_id)
    closed = iteration_store.close_succeeded(
        seg.id,
        outcomes='[{"outcome": "reducer pass outcome"}]',
        closed_at=datetime.now(UTC),
    )
    assert closed.status == IterationStatus.SUCCEEDED
    assert closed.outcomes == '[{"outcome": "reducer pass outcome"}]'
    assert closed.closed_at is not None


def test_close_succeeded_persists_through_get(
    workflow_store, iteration_store, task_center_run_id
):
    seg = _seed_segment(workflow_store, iteration_store, task_center_run_id)
    iteration_store.close_succeeded(
        seg.id,
        outcomes='[{"outcome": "outcome"}]',
    )
    reloaded = iteration_store.get(seg.id)
    assert reloaded is not None
    assert reloaded.outcomes == '[{"outcome": "outcome"}]'


def test_failed_close_leaves_outcomes_null(
    workflow_store, iteration_store, task_center_run_id
):
    seg = _seed_segment(workflow_store, iteration_store, task_center_run_id)
    failed = iteration_store.set_status(
        seg.id,
        status=IterationStatus.FAILED,
        closed_at=datetime.now(UTC),
    )
    assert failed.status == IterationStatus.FAILED
    assert failed.outcomes is None


def test_close_succeeded_unknown_segment_raises(iteration_store):
    with pytest.raises(LookupError):
        iteration_store.close_succeeded(
            "no-such-iteration",
            outcomes="[]",
        )


def test_initial_iteration_has_null_outcomes(
    workflow_store, iteration_store, task_center_run_id
):
    seg = _seed_segment(workflow_store, iteration_store, task_center_run_id)
    assert seg.outcomes is None
