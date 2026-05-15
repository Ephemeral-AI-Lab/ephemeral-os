"""Persistence tests for GoalStore."""

from __future__ import annotations

from datetime import UTC, datetime

from task_center.goal.state import (
    Goal,
    GoalStatus,
)


def test_insert_returns_dto(goal_store, task_center_run_id):
    req = goal_store.insert(
        task_center_run_id=task_center_run_id,
        requested_by_task_id="t1",
        goal="g",
    )
    assert isinstance(req, Goal)
    assert req.is_open
    assert req.iteration_ids == ()


def test_get_round_trip(goal_store, task_center_run_id):
    inserted = goal_store.insert(
        task_center_run_id=task_center_run_id,
        requested_by_task_id="t1",
        goal="g",
    )
    got = goal_store.get(inserted.id)
    assert got is not None
    assert got.id == inserted.id
    assert got.goal == "g"
    assert got.requested_by_task_id == "t1"
    assert got.iteration_ids == ()


def test_append_iteration_id_persists_tuple(goal_store, task_center_run_id):
    req = goal_store.insert(
        task_center_run_id=task_center_run_id,
        requested_by_task_id="t1",
        goal="g",
    )
    after_first = goal_store.append_iteration_id(req.id, "s1")
    after_second = goal_store.append_iteration_id(req.id, "s2")
    assert after_first.iteration_ids == ("s1",)
    assert after_second.iteration_ids == ("s1", "s2")
    assert isinstance(after_second.iteration_ids, tuple)


def test_set_status_records_outcome_and_closed_at(
    goal_store, task_center_run_id
):
    req = goal_store.insert(
        task_center_run_id=task_center_run_id,
        requested_by_task_id="t1",
        goal="g",
    )
    closed_at = datetime.now(UTC)
    updated = goal_store.set_status(
        req.id,
        status=GoalStatus.SUCCEEDED,
        final_outcome={"outcome": "success"},
        closed_at=closed_at,
    )
    assert updated.status == GoalStatus.SUCCEEDED
    assert updated.final_outcome == {"outcome": "success"}
    assert updated.closed_at is not None


def test_list_for_executor_task_orders_by_created_at(
    goal_store, task_center_run_id
):
    a = goal_store.insert(
        task_center_run_id=task_center_run_id,
        requested_by_task_id="executor-A",
        goal="ga",
    )
    b = goal_store.insert(
        task_center_run_id=task_center_run_id,
        requested_by_task_id="executor-A",
        goal="gb",
    )
    goal_store.insert(
        task_center_run_id=task_center_run_id,
        requested_by_task_id="executor-B",
        goal="gc",
    )
    listed = goal_store.list_for_executor_task("executor-A")
    assert [r.id for r in listed] == [a.id, b.id]
