"""Persistence tests for MissionStore."""

from __future__ import annotations

from datetime import UTC, datetime

from task_center.mission.mission import (
    Mission,
    MissionStatus,
)


def test_insert_returns_dto(mission_store, task_center_run_id):
    req = mission_store.insert(
        task_center_run_id=task_center_run_id,
        requested_by_task_id="t1",
        goal="g",
    )
    assert isinstance(req, Mission)
    assert req.is_open
    assert req.episode_ids == ()


def test_get_round_trip(mission_store, task_center_run_id):
    inserted = mission_store.insert(
        task_center_run_id=task_center_run_id,
        requested_by_task_id="t1",
        goal="g",
    )
    got = mission_store.get(inserted.id)
    assert got is not None
    assert got.id == inserted.id
    assert got.goal == "g"
    assert got.requested_by_task_id == "t1"
    assert got.episode_ids == ()


def test_append_episode_id_persists_tuple(mission_store, task_center_run_id):
    req = mission_store.insert(
        task_center_run_id=task_center_run_id,
        requested_by_task_id="t1",
        goal="g",
    )
    after_first = mission_store.append_episode_id(req.id, "s1")
    after_second = mission_store.append_episode_id(req.id, "s2")
    assert after_first.episode_ids == ("s1",)
    assert after_second.episode_ids == ("s1", "s2")
    assert isinstance(after_second.episode_ids, tuple)


def test_set_status_records_outcome_and_closed_at(
    mission_store, task_center_run_id
):
    req = mission_store.insert(
        task_center_run_id=task_center_run_id,
        requested_by_task_id="t1",
        goal="g",
    )
    closed_at = datetime.now(UTC)
    updated = mission_store.set_status(
        req.id,
        status=MissionStatus.SUCCEEDED,
        final_outcome={"outcome": "success"},
        closed_at=closed_at,
    )
    assert updated.status == MissionStatus.SUCCEEDED
    assert updated.final_outcome == {"outcome": "success"}
    assert updated.closed_at is not None


def test_list_for_executor_task_orders_by_created_at(
    mission_store, task_center_run_id
):
    a = mission_store.insert(
        task_center_run_id=task_center_run_id,
        requested_by_task_id="executor-A",
        goal="ga",
    )
    b = mission_store.insert(
        task_center_run_id=task_center_run_id,
        requested_by_task_id="executor-A",
        goal="gb",
    )
    mission_store.insert(
        task_center_run_id=task_center_run_id,
        requested_by_task_id="executor-B",
        goal="gc",
    )
    listed = mission_store.list_for_executor_task("executor-A")
    assert [r.id for r in listed] == [a.id, b.id]
