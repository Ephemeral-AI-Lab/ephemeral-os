"""Persistence tests for AttemptStore."""

from __future__ import annotations

from task_center._core.state import (
    Attempt,
    AttemptFailReason,
    AttemptStage,
    AttemptStatus,
    IterationCreationReason,
)


def _seed_segment(
    workflow_store, iteration_store, task_center_run_id, sequence_no=1
) -> str:
    req = workflow_store.insert(
        task_center_run_id=task_center_run_id,
        parent_task_id="t1",
        workflow_goal="g",
    )
    seg = iteration_store.insert(
        workflow_id=req.id,
        sequence_no=sequence_no,
        creation_reason=IterationCreationReason.INITIAL,
        iteration_goal="g",
        attempt_budget=2,
    )
    return seg.id


def test_insert_returns_running_planning_dto(
    attempt_store, iteration_store, workflow_store, task_center_run_id
):
    seg_id = _seed_segment(workflow_store, iteration_store, task_center_run_id)
    g = attempt_store.insert(iteration_id=seg_id, attempt_sequence_no=1)
    assert isinstance(g, Attempt)
    assert g.stage == AttemptStage.PLAN
    assert g.status == AttemptStatus.RUNNING
    assert g.generator_task_ids == ()
    assert g.reducer_task_ids == ()
    assert g.fail_reason is None


def test_set_deferred_goal_and_reducer_task_ids_persist(
    attempt_store, iteration_store, workflow_store, task_center_run_id
):
    seg_id = _seed_segment(workflow_store, iteration_store, task_center_run_id)
    g = attempt_store.insert(iteration_id=seg_id, attempt_sequence_no=1)
    g = attempt_store.set_deferred_goal(
        g.id,
        deferred_goal_for_next_iteration="next",
    )
    assert g.deferred_goal_for_next_iteration == "next"
    g = attempt_store.set_reducer_task_ids(g.id, ["r1", "r2"])
    assert g.reducer_task_ids == ("r1", "r2")


def test_close_records_status_fail_reason_and_closed_at(
    attempt_store, iteration_store, workflow_store, task_center_run_id
):
    seg_id = _seed_segment(workflow_store, iteration_store, task_center_run_id)
    g = attempt_store.insert(iteration_id=seg_id, attempt_sequence_no=1)
    closed = attempt_store.close(
        g.id,
        status=AttemptStatus.FAILED,
        fail_reason=AttemptFailReason.TASK_FAILED,
    )
    assert closed.is_closed
    assert closed.status == AttemptStatus.FAILED
    assert closed.fail_reason == AttemptFailReason.TASK_FAILED
    assert closed.closed_at is not None


def test_list_for_iteration_orders_by_attempt_sequence_no(
    attempt_store, iteration_store, workflow_store, task_center_run_id
):
    seg_id = _seed_segment(workflow_store, iteration_store, task_center_run_id)
    g2 = attempt_store.insert(iteration_id=seg_id, attempt_sequence_no=2)
    g1 = attempt_store.insert(iteration_id=seg_id, attempt_sequence_no=1)
    listed = attempt_store.list_for_iteration(seg_id)
    assert [g.id for g in listed] == [g1.id, g2.id]


def test_get_by_sequence(
    attempt_store, iteration_store, workflow_store, task_center_run_id
):
    seg_id = _seed_segment(workflow_store, iteration_store, task_center_run_id)
    g = attempt_store.insert(iteration_id=seg_id, attempt_sequence_no=1)
    found = attempt_store.get_by_sequence(
        iteration_id=seg_id, attempt_sequence_no=1
    )
    assert found is not None and found.id == g.id
    missing = attempt_store.get_by_sequence(
        iteration_id=seg_id, attempt_sequence_no=99
    )
    assert missing is None
