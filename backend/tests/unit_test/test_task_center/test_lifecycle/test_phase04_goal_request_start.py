"""Phase 04 goal request starter tests.

Covers happy path, startup failure rollback, and duplicate-open-request gating.
"""

from __future__ import annotations

import pytest

from task_center.goal.starter import (
    GoalStarter,
    StartedGoal,
)
from task_center.goal.state import GoalStatus
from task_center._core.types import TaskCenterInvariantViolation
from task_center.trial.orchestrator_registry import (
    TrialOrchestratorRegistry,
)
from task_center.trial.runtime import AgentLaunch, TrialDeps
from task_center.trial import (
    TrialFailReason,
    TrialStatus,
)
from task_center.iteration import IterationManagerRegistry
from task_center.iteration.state import (
    IterationCreationReason,
    IterationStatus,
    )
from task_center.task_state import TaskCenterTaskRole, TaskCenterTaskStatus
from task_center._core.types import planner_task_id


class _FakeLauncher:
    def __init__(self) -> None:
        self.launches: list[AgentLaunch] = []

    def launch(self, launch: AgentLaunch) -> None:
        self.launches.append(launch)


class _FailingLauncher:
    def launch(self, launch: AgentLaunch) -> None:
        del launch
        raise RuntimeError("delegated planner launch boom")


def _build_runtime(
    mission_store, episode_store, attempt_store, task_store, *, composer, launcher=None
) -> TrialDeps:
    launcher = launcher or _FakeLauncher()
    registry = TrialOrchestratorRegistry()
    return TrialDeps(
        goal_store=mission_store,
        iteration_store=episode_store,
        trial_store=attempt_store,
        task_store=task_store,
        agent_launcher=launcher,
        orchestrator_registry=registry,
        manager_registry=IterationManagerRegistry(),
        composer=composer,
    )


def _seed_outer_generator_task(
    *,
    task_store,
    goal_store,
    iteration_store,
    trial_store,
    task_center_run_id: str,
) -> tuple[str, str]:
    """Seed an outer generator task whose attempt is currently RUNNING."""
    outer_request = goal_store.insert(
        task_center_run_id=task_center_run_id,
        requested_by_task_id="root",
        goal="outer goal",
    )
    outer_segment = iteration_store.insert(
        goal_id=outer_request.id,
        sequence_no=1,
        creation_reason=IterationCreationReason.INITIAL,
        goal="outer goal",
        trial_budget=2,
    )
    goal_store.append_iteration_id(outer_request.id, outer_segment.id)
    outer_attempt = trial_store.insert(
        iteration_id=outer_segment.id, trial_sequence_no=1
    )
    iteration_store.append_trial_id(outer_segment.id, outer_attempt.id)

    parent_task_id = "outer-generator-task"
    task_store.upsert_task(
        task_id=parent_task_id,
        task_center_run_id=task_center_run_id,
        role=TaskCenterTaskRole.GENERATOR.value,
        agent_name="executor",
        rendered_prompt="execute the outer task",
        status=TaskCenterTaskStatus.RUNNING.value,
        summaries=[],
        needs=[],
        task_center_attempt_id=outer_attempt.id,
        spawn_reason="trial_generator",
    )
    return parent_task_id, outer_attempt.id


def test_mission_start_creates_request_segment_graph_and_marks_parent_waiting(
    mission_store, episode_store, attempt_store, task_store, task_center_run_id, composer
) -> None:
    runtime = _build_runtime(
        mission_store, episode_store, attempt_store, task_store, composer=composer
    )
    parent_task_id, parent_attempt_id = _seed_outer_generator_task(
        task_store=task_store,
        goal_store=mission_store,
        iteration_store=episode_store,
        trial_store=attempt_store,
        task_center_run_id=task_center_run_id,
    )
    coordinator = GoalStarter(runtime=runtime)

    result: StartedGoal = coordinator.start(
        parent_task_id=parent_task_id,
        goal="solve delegated task",
    )

    delegated_request = mission_store.get(result.goal_id)
    initial_episode = episode_store.get(result.initial_iteration_id)
    initial_graph = attempt_store.get(result.initial_trial_id)
    parent_task = task_store.get_task(parent_task_id)

    assert delegated_request is not None
    assert delegated_request.status == GoalStatus.OPEN
    assert delegated_request.requested_by_task_id == parent_task_id
    assert delegated_request.goal == "solve delegated task"
    assert initial_episode is not None
    assert initial_episode.goal_id == delegated_request.id
    assert initial_graph is not None
    assert initial_graph.iteration_id == initial_episode.id
    assert parent_task is not None
    assert parent_task["status"] == TaskCenterTaskStatus.WAITING_MISSION.value
    # Delegated orchestrator was started.
    assert runtime.orchestrator_registry.get(initial_graph.id) is not None


def test_mission_start_startup_failure_leaves_parent_running(
    mission_store, episode_store, attempt_store, task_store, task_center_run_id, composer
) -> None:
    runtime = _build_runtime(
        mission_store, episode_store, attempt_store, task_store, composer=composer
    )
    parent_task_id, parent_attempt_id = _seed_outer_generator_task(
        task_store=task_store,
        goal_store=mission_store,
        iteration_store=episode_store,
        trial_store=attempt_store,
        task_center_run_id=task_center_run_id,
    )

    def _failing_factory(attempt, on_attempt_closed):
        del attempt, on_attempt_closed
        raise RuntimeError("delegated startup boom")

    coordinator = GoalStarter(runtime=runtime)
    # Patch the factory used by the coordinator's handler builder.
    original = GoalStarter._build_handler

    def _patched_build_handler(self):
        handler = original(self)
        handler._factory._orchestrator_factory = _failing_factory  # type: ignore[attr-defined]
        return handler

    GoalStarter._build_handler = _patched_build_handler  # type: ignore[assignment]
    try:
        with pytest.raises(RuntimeError):
            coordinator.start(
                parent_task_id=parent_task_id,
                goal="delegated",
            )
    finally:
        GoalStarter._build_handler = original  # type: ignore[assignment]

    parent_task = task_store.get_task(parent_task_id)
    assert parent_task is not None
    assert parent_task["status"] == TaskCenterTaskStatus.RUNNING.value
    # The compensation path must mark the request and iteration cancelled.
    open_requests = [
        r
        for r in mission_store.list_for_executor_task(parent_task_id)
        if r.is_open
    ]
    assert open_requests == []
    cancelled = [
        r
        for r in mission_store.list_for_executor_task(parent_task_id)
        if r.status == GoalStatus.CANCELLED
    ]
    assert len(cancelled) == 1
    assert cancelled[0].requested_by_task_id == parent_task_id
    cancelled_segment = episode_store.list_for_goal(cancelled[0].id)
    assert len(cancelled_segment) == 1
    assert cancelled_segment[0].status == IterationStatus.CANCELLED
    assert runtime.manager_registry is not None
    assert runtime.manager_registry.get(cancelled_segment[0].id) is None


def test_mission_start_startup_failure_closes_started_graph_and_deregisters_orchestrator(
    mission_store, episode_store, attempt_store, task_store, task_center_run_id, composer
) -> None:
    runtime = _build_runtime(
        mission_store,
        episode_store,
        attempt_store,
        task_store,
        launcher=_FailingLauncher(),
        composer=composer,
    )
    parent_task_id, parent_attempt_id = _seed_outer_generator_task(
        task_store=task_store,
        goal_store=mission_store,
        iteration_store=episode_store,
        trial_store=attempt_store,
        task_center_run_id=task_center_run_id,
    )
    coordinator = GoalStarter(runtime=runtime)

    with pytest.raises(RuntimeError):
        coordinator.start(
            parent_task_id=parent_task_id,
            goal="delegated",
        )

    [cancelled_request] = [
        r
        for r in mission_store.list_for_executor_task(parent_task_id)
        if r.status == GoalStatus.CANCELLED
    ]
    [cancelled_segment] = episode_store.list_for_goal(cancelled_request.id)
    [failed_attempt] = attempt_store.list_for_iteration(cancelled_segment.id)
    assert failed_attempt.status == TrialStatus.FAILED
    assert failed_attempt.fail_reason == TrialFailReason.STARTUP_FAILED
    assert runtime.orchestrator_registry.get(failed_attempt.id) is None
    assert runtime.manager_registry is not None
    assert runtime.manager_registry.get(cancelled_segment.id) is None
    planner_task = task_store.get_task(planner_task_id(failed_attempt.id))
    assert planner_task is not None
    assert planner_task["status"] == TaskCenterTaskStatus.FAILED.value


def test_mission_start_rejects_second_open_child_request_for_same_executor(
    mission_store, episode_store, attempt_store, task_store, task_center_run_id, composer
) -> None:
    runtime = _build_runtime(
        mission_store, episode_store, attempt_store, task_store, composer=composer
    )
    parent_task_id, parent_attempt_id = _seed_outer_generator_task(
        task_store=task_store,
        goal_store=mission_store,
        iteration_store=episode_store,
        trial_store=attempt_store,
        task_center_run_id=task_center_run_id,
    )
    coordinator = GoalStarter(runtime=runtime)
    coordinator.start(
        parent_task_id=parent_task_id,
        goal="first delegation",
    )

    # Restore the parent to running so the second call passes the running gate
    # but is rejected by the duplicate-open-request check.
    task_store.set_task_status(
        parent_task_id,
        status=TaskCenterTaskStatus.RUNNING.value,
    )

    with pytest.raises(TaskCenterInvariantViolation) as exc:
        coordinator.start(
            parent_task_id=parent_task_id,
            goal="second delegation",
        )
    assert "open delegated goal" in str(exc.value)


def test_mission_start_rejects_non_running_parent(
    mission_store, episode_store, attempt_store, task_store, task_center_run_id, composer
) -> None:
    runtime = _build_runtime(
        mission_store, episode_store, attempt_store, task_store, composer=composer
    )
    parent_task_id, parent_attempt_id = _seed_outer_generator_task(
        task_store=task_store,
        goal_store=mission_store,
        iteration_store=episode_store,
        trial_store=attempt_store,
        task_center_run_id=task_center_run_id,
    )
    task_store.set_task_status(
        parent_task_id, status=TaskCenterTaskStatus.DONE.value
    )

    coordinator = GoalStarter(runtime=runtime)
    with pytest.raises(TaskCenterInvariantViolation) as exc:
        coordinator.start(
            parent_task_id=parent_task_id,
            goal="delegated",
        )
    assert "not running" in str(exc.value)


def test_mission_start_accepts_entry_mode_caller_with_no_parent_attempt(
    mission_store, episode_store, attempt_store, task_store, task_center_run_id, composer
) -> None:
    """Entry-mode caller has ``parent_trial_id=None``.

    The goal starter must accept that and route the parent-waiting
    transition through the runtime's :class:`EntryTaskController` so the
    controller stays the single owner of entry-task state transitions.
    """
    from task_center.entry import EntryTaskController

    # Seed the entry-mode caller: an entry task with task_center_attempt_id=None.
    entry_task_id = "entry-task-id"
    manager_registry = IterationManagerRegistry()
    task_store.upsert_task(
        task_id=entry_task_id,
        task_center_run_id=task_center_run_id,
        role=TaskCenterTaskRole.GENERATOR.value,
        agent_name="entry_executor",
        rendered_prompt="entry goal",
        status=TaskCenterTaskStatus.RUNNING.value,
        summaries=[],
        needs=[],
        task_center_attempt_id=None,
        spawn_reason="entry_executor",
    )
    controller = EntryTaskController(
        task_id=entry_task_id,
        task_center_run_id=task_center_run_id,
        task_store=task_store,
    )
    runtime = TrialDeps(
        goal_store=mission_store,
        iteration_store=episode_store,
        trial_store=attempt_store,
        task_store=task_store,
        agent_launcher=_FakeLauncher(),
        orchestrator_registry=TrialOrchestratorRegistry(),
        manager_registry=manager_registry,
        composer=composer,
        entry_task_controller=controller,
    )

    coordinator = GoalStarter(runtime=runtime)
    result: StartedGoal = coordinator.start(
        parent_task_id=entry_task_id,
        goal="solve delegated work",
    )

    # Entry task is now WAITING_MISSION via the controller.
    entry_task = task_store.get_task(entry_task_id)
    assert entry_task is not None
    assert entry_task["status"] == TaskCenterTaskStatus.WAITING_MISSION.value
    # Result carries None for parent_attempt_id (entry mode).
    assert result.parent_attempt_id is None
    # Delegated request + iteration + attempt were all created and started.
    delegated_request = mission_store.get(result.goal_id)
    delegated_segment = episode_store.get(result.initial_iteration_id)
    delegated_attempt = attempt_store.get(result.initial_trial_id)
    assert delegated_request is not None
    assert delegated_request.status == GoalStatus.OPEN
    assert delegated_segment is not None
    assert delegated_attempt is not None
    assert runtime.orchestrator_registry.get(delegated_attempt.id) is not None
