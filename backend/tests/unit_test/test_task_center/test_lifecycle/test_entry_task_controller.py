"""EntryTaskController unit tests.

Confirms the controller is the single owner of attempt-less entry-executor
lifecycle: terminal submissions, run exhaustion, delegated-close-report
resume, and waiting-for-mission-start transitions. The entry episode never gets a
Attempt row in any of these tests.
"""

from __future__ import annotations

import pytest

from task_center.mission.handler import MissionHandler
from task_center.mission.mission import (
    MissionCloseReport,
    MissionStatus,
)
from task_center.config import HarnessLifecycleConfig
from task_center.entry.controller import EntryTaskController
from task_center.exceptions import TaskCenterInvariantViolation
from task_center.episode.registry import EpisodeManagerRegistry
from task_center.episode.episode import EpisodeStatus
from task_center.task import HarnessTaskRole, HarnessTaskStatus


def _seed_entry(*, mission_store, task_center_run_id):
    """Seed the entry-mode complex_request used by every test in this file."""
    entry_task_id = f"{task_center_run_id}:entry"
    request = mission_store.insert(
        task_center_run_id=task_center_run_id,
        requested_by_task_id=entry_task_id,
        goal="entry goal",
    )
    return entry_task_id, request


def _build_controller(
    *,
    entry_task_id,
    task_center_run_id,
    request,
    mission_store,
    episode_store,
    attempt_store,
    task_store,
    finished_runs,
):
    handler = MissionHandler(
        mission_store=mission_store,
        episode_store=episode_store,
        attempt_store=attempt_store,
        manager_registry=EpisodeManagerRegistry(),
        config=HarnessLifecycleConfig(),
        deliver_close_report=lambda report: finished_runs.append(report),
    )
    episode, _manager = handler.create_initial_episode_with_manager(
        mission_id=request.id
    )
    task_store.upsert_task(
        task_id=entry_task_id,
        task_center_run_id=task_center_run_id,
        role=HarnessTaskRole.GENERATOR.value,
        agent_name="entry_executor",
        task_input="entry goal",
        status=HarnessTaskStatus.RUNNING.value,
        summaries=[],
        needs=[],
        task_center_attempt_id=None,
        spawn_reason="entry_executor",
    )
    controller = EntryTaskController(
        task_id=entry_task_id,
        task_center_run_id=task_center_run_id,
        mission_id=request.id,
        episode_id=episode.id,
        task_store=task_store,
        episode_store=episode_store,
        mission_handler=handler,
        manager_registry=handler._manager_registry,  # type: ignore[attr-defined]
    )
    return controller, episode


@pytest.fixture
def entry_setup(
    mission_store,
    episode_store,
    attempt_store,
    task_store,
    task_center_run_id,
):
    entry_task_id, request = _seed_entry(
        mission_store=mission_store,
        task_center_run_id=task_center_run_id,
    )
    finished_runs: list = []
    controller, episode = _build_controller(
        entry_task_id=entry_task_id,
        task_center_run_id=task_center_run_id,
        request=request,
        mission_store=mission_store,
        episode_store=episode_store,
        attempt_store=attempt_store,
        task_store=task_store,
        finished_runs=finished_runs,
    )
    return controller, episode, request, finished_runs


def test_apply_executor_success_marks_episode_request_done(
    entry_setup, task_store, episode_store, mission_store, attempt_store
):
    controller, episode, request, finished_runs = entry_setup

    controller.apply_executor_success(summary="all good", artifacts=["a.md"])

    task = task_store.get_task(controller.task_id)
    fresh_segment = episode_store.get(episode.id)
    fresh_request = mission_store.get(request.id)
    assert task is not None
    assert task["status"] == HarnessTaskStatus.DONE.value
    assert fresh_segment is not None
    assert fresh_segment.status == EpisodeStatus.SUCCEEDED
    assert fresh_segment.task_specification == "all good"
    assert fresh_segment.task_summary == "all good"
    assert fresh_request is not None
    assert fresh_request.status == MissionStatus.SUCCEEDED
    # Entry episode never gets a attempt row.
    assert attempt_store.list_for_episode(episode.id) == []
    # close-report delivered → caller can finish the run.
    assert len(finished_runs) == 1
    assert finished_runs[0].outcome == "success"
    assert finished_runs[0].final_attempt_id is None


def test_apply_executor_failure_marks_episode_request_failed(
    entry_setup, task_store, episode_store, mission_store
):
    controller, episode, request, finished_runs = entry_setup

    controller.apply_executor_failure(
        summary="cannot proceed",
        reason="missing input",
        details=["no goal"],
    )

    task = task_store.get_task(controller.task_id)
    fresh_segment = episode_store.get(episode.id)
    fresh_request = mission_store.get(request.id)
    assert task is not None
    assert task["status"] == HarnessTaskStatus.FAILED.value
    assert fresh_segment is not None
    assert fresh_segment.status == EpisodeStatus.FAILED
    assert fresh_request is not None
    assert fresh_request.status == MissionStatus.FAILED
    assert len(finished_runs) == 1
    assert finished_runs[0].outcome == "failed"


def test_apply_run_exhausted_marks_failed(
    entry_setup, task_store, episode_store, mission_store
):
    controller, episode, request, finished_runs = entry_setup

    controller.apply_run_exhausted(summary="agent ran without terminal")

    task = task_store.get_task(controller.task_id)
    fresh_segment = episode_store.get(episode.id)
    fresh_request = mission_store.get(request.id)
    assert task is not None
    assert task["status"] == HarnessTaskStatus.FAILED.value
    last = task["summaries"][-1]
    assert last["fail_reason"] == "run_exhausted"
    assert fresh_segment is not None
    assert fresh_segment.status == EpisodeStatus.FAILED
    assert fresh_request is not None
    assert fresh_request.status == MissionStatus.FAILED
    assert len(finished_runs) == 1
    assert finished_runs[0].outcome == "failed"


def test_mark_waiting_then_close_report_success(
    entry_setup, task_store, episode_store, mission_store
):
    controller, episode, request, finished_runs = entry_setup
    controller.mark_waiting_mission(
        delegated_mission_id="delegated-1",
        delegated_episode_id="delegated-seg",
        delegated_attempt_id="delegated-attempt",
        goal="solve x",
    )
    task = task_store.get_task(controller.task_id)
    assert task is not None
    assert task["status"] == HarnessTaskStatus.WAITING_COMPLEX_TASK.value

    controller.apply_mission_close_report(
        MissionCloseReport(
            mission_id="delegated-1",
            requested_by_task_id=controller.task_id,
            outcome="success",
            final_episode_id="delegated-seg",
            final_attempt_id="delegated-attempt",
        )
    )

    task = task_store.get_task(controller.task_id)
    fresh_segment = episode_store.get(episode.id)
    fresh_request = mission_store.get(request.id)
    assert task is not None
    assert task["status"] == HarnessTaskStatus.DONE.value
    assert fresh_segment is not None
    assert fresh_segment.status == EpisodeStatus.SUCCEEDED
    assert fresh_request is not None
    assert fresh_request.status == MissionStatus.SUCCEEDED
    assert len(finished_runs) == 1
    assert finished_runs[0].outcome == "success"


def test_close_report_failure_marks_failed(entry_setup, task_store, mission_store):
    controller, _segment, request, finished_runs = entry_setup
    controller.mark_waiting_mission(
        delegated_mission_id="delegated-1",
        delegated_episode_id="delegated-seg",
        delegated_attempt_id="delegated-attempt",
        goal="solve x",
    )

    controller.apply_mission_close_report(
        MissionCloseReport(
            mission_id="delegated-1",
            requested_by_task_id=controller.task_id,
            outcome="failed",
            final_episode_id="delegated-seg",
            final_attempt_id="delegated-attempt",
        )
    )

    task = task_store.get_task(controller.task_id)
    fresh_request = mission_store.get(request.id)
    assert task is not None
    assert task["status"] == HarnessTaskStatus.FAILED.value
    assert fresh_request is not None
    assert fresh_request.status == MissionStatus.FAILED
    assert len(finished_runs) == 1
    assert finished_runs[0].outcome == "failed"


def test_close_report_idempotent_when_task_already_terminal(
    entry_setup, task_store, mission_store
):
    controller, _segment, request, finished_runs = entry_setup
    # Drive the entry task straight to DONE via apply_executor_success first.
    controller.apply_executor_success(summary="done", artifacts=[])
    assert len(finished_runs) == 1

    # A late delegated close-report must not double-finish or raise.
    controller.apply_mission_close_report(
        MissionCloseReport(
            mission_id="delegated-1",
            requested_by_task_id=controller.task_id,
            outcome="failed",
            final_episode_id="delegated-seg",
            final_attempt_id=None,
        )
    )

    task = task_store.get_task(controller.task_id)
    fresh_request = mission_store.get(request.id)
    assert task is not None
    assert task["status"] == HarnessTaskStatus.DONE.value
    assert fresh_request is not None
    # Late close-report did not flip the request status away from SUCCEEDED.
    assert fresh_request.status == MissionStatus.SUCCEEDED
    assert len(finished_runs) == 1


def test_mark_waiting_rejects_when_task_is_not_running(entry_setup, task_store):
    controller, _segment, _request, _finished = entry_setup
    # Force the task off RUNNING.
    task_store.set_task_status(
        controller.task_id, status=HarnessTaskStatus.DONE.value
    )

    with pytest.raises(TaskCenterInvariantViolation):
        controller.mark_waiting_mission(
            delegated_mission_id="r",
            delegated_episode_id="s",
            delegated_attempt_id="g",
            goal="g",
        )


def test_restore_running_after_failed_mission_start_rolls_back_waiting(
    entry_setup, task_store
):
    controller, _segment, _request, _finished = entry_setup
    controller.mark_waiting_mission(
        delegated_mission_id="r",
        delegated_episode_id="s",
        delegated_attempt_id="g",
        goal="g",
    )
    assert (
        task_store.get_task(controller.task_id)["status"]
        == HarnessTaskStatus.WAITING_COMPLEX_TASK.value
    )

    controller.restore_running_after_failed_mission_start()

    assert (
        task_store.get_task(controller.task_id)["status"]
        == HarnessTaskStatus.RUNNING.value
    )


def test_terminal_is_idempotent(entry_setup, task_store, mission_store):
    controller, _segment, request, finished_runs = entry_setup

    controller.apply_executor_success(summary="ok", artifacts=[])
    controller.apply_executor_success(summary="ok again", artifacts=[])

    task = task_store.get_task(controller.task_id)
    fresh_request = mission_store.get(request.id)
    assert task is not None
    assert task["status"] == HarnessTaskStatus.DONE.value
    # Request was closed once; second call must not raise (idempotent close).
    assert fresh_request is not None
    assert fresh_request.status == MissionStatus.SUCCEEDED
    # close-report delivered exactly once.
    assert len(finished_runs) == 1
