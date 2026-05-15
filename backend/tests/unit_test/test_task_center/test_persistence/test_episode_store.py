"""Persistence tests for IterationStore."""

from __future__ import annotations

from datetime import UTC, datetime

from task_center.iteration.state import (
    Iteration,
    IterationCreationReason,
    IterationStatus,
)


def _seed_request(mission_store, task_center_run_id) -> str:
    req = mission_store.insert(
        task_center_run_id=task_center_run_id,
        requested_by_task_id="t1",
        goal="g",
    )
    return req.id


def test_insert_returns_dto(episode_store, mission_store, task_center_run_id):
    request_id = _seed_request(mission_store, task_center_run_id)
    seg = episode_store.insert(
        goal_id=request_id,
        sequence_no=1,
        creation_reason=IterationCreationReason.INITIAL,
        goal="g",
        trial_budget=2,
    )
    assert isinstance(seg, Iteration)
    assert seg.is_open
    assert segtrial_ids == ()
    assert segtrial_budget == 2


def test_get_round_trip(episode_store, mission_store, task_center_run_id):
    request_id = _seed_request(mission_store, task_center_run_id)
    inserted = episode_store.insert(
        goal_id=request_id,
        sequence_no=1,
        creation_reason=IterationCreationReason.INITIAL,
        goal="g",
        trial_budget=2,
    )
    got = episode_store.get(inserted.id)
    assert got is not None
    assert got.id == inserted.id
    assert got.creation_reason == IterationCreationReason.INITIAL


def test_append_attempt_id_preserves_order(
    episode_store, mission_store, task_center_run_id
):
    request_id = _seed_request(mission_store, task_center_run_id)
    seg = episode_store.insert(
        goal_id=request_id,
        sequence_no=1,
        creation_reason=IterationCreationReason.INITIAL,
        goal="g",
        trial_budget=3,
    )
    s1 = episode_store.append_attempt_id(seg.id, "g1")
    s2 = episode_store.append_attempt_id(seg.id, "g2")
    assert s1trial_ids == ("g1",)
    assert s2trial_ids == ("g1", "g2")
    assert s2trial_count == 2


def test_set_continuation_goal_and_status(
    episode_store, mission_store, task_center_run_id
):
    request_id = _seed_request(mission_store, task_center_run_id)
    seg = episode_store.insert(
        goal_id=request_id,
        sequence_no=1,
        creation_reason=IterationCreationReason.INITIAL,
        goal="g",
        trial_budget=2,
    )
    seg = episode_store.set_continuation_goal(seg.id, "next-goal")
    assert seg.continuation_goal == "next-goal"
    seg = episode_store.set_status(
        seg.id,
        status=IterationStatus.SUCCEEDED,
        closed_at=datetime.now(UTC),
    )
    assert seg.status == IterationStatus.SUCCEEDED
    assert seg.closed_at is not None


def test_list_for_mission_orders_by_sequence_no(
    episode_store, mission_store, task_center_run_id
):
    request_id = _seed_request(mission_store, task_center_run_id)
    s2 = episode_store.insert(
        goal_id=request_id,
        sequence_no=2,
        creation_reason=IterationCreationReason.PARTIAL_CONTINUATION,
        goal="g2",
        trial_budget=2,
    )
    s1 = episode_store.insert(
        goal_id=request_id,
        sequence_no=1,
        creation_reason=IterationCreationReason.INITIAL,
        goal="g1",
        trial_budget=2,
    )
    listed = episode_store.list_for_mission(request_id)
    assert [s.id for s in listed] == [s1.id, s2.id]


def test_get_by_sequence(episode_store, mission_store, task_center_run_id):
    request_id = _seed_request(mission_store, task_center_run_id)
    seg = episode_store.insert(
        goal_id=request_id,
        sequence_no=1,
        creation_reason=IterationCreationReason.INITIAL,
        goal="g",
        trial_budget=2,
    )
    found = episode_store.get_by_sequence(
        goal_id=request_id, sequence_no=1
    )
    assert found is not None
    assert found.id == seg.id
    missing = episode_store.get_by_sequence(
        goal_id=request_id, sequence_no=99
    )
    assert missing is None
