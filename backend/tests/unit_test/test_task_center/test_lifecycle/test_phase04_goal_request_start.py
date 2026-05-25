"""Phase 04 goal request starter tests.

Covers happy path, startup failure rollback, and duplicate-open-request gating.
"""

from __future__ import annotations

import pytest

from task_center.goal.starter import (
    GoalStarter,
    StartedGoal,
)
from task_center.goal.state import GoalOrigin, GoalOriginKind, GoalStatus
from task_center._core.primitives import TaskCenterInvariantViolation
from task_center.attempt.orchestrator_registry import (
    AttemptOrchestratorRegistry,
)
from task_center.attempt.runtime import AgentLaunch, AttemptDeps
from task_center.attempt import (
    AttemptFailReason,
    AttemptStatus,
)
from task_center.iteration import OpenIterationCoordinatorRegistry
from task_center.iteration.state import (
    IterationCreationReason,
    IterationStatus,
)
from task_center.task_state import TaskCenterTaskRole, TaskCenterBackgroundTaskStatus
from task_center._core.primitives import planner_task_id


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
    goal_store, iteration_store, attempt_store, task_store, *, composer, launcher=None
) -> AttemptDeps:
    launcher = launcher or _FakeLauncher()
    registry = AttemptOrchestratorRegistry()
    return AttemptDeps(
        goal_store=goal_store,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        task_store=task_store,
        agent_launcher=launcher,
        orchestrator_registry=registry,
        iteration_coordinators=OpenIterationCoordinatorRegistry(),
        composer=composer,
    )


def _seed_outer_generator_task(
    *,
    task_store,
    goal_store,
    iteration_store,
    attempt_store,
    task_center_run_id: str,
) -> tuple[str, str]:
    """Seed an outer generator task whose attempt is currently RUNNING."""
    outer_request = goal_store.insert(
        task_center_run_id=task_center_run_id,
        requested_by_task_id="parent-task",
        goal="outer goal",
    )
    outer_segment = iteration_store.insert(
        goal_id=outer_request.id,
        sequence_no=1,
        creation_reason=IterationCreationReason.INITIAL,
        goal="outer goal",
        attempt_budget=2,
    )
    goal_store.append_iteration_id(outer_request.id, outer_segment.id)
    outer_attempt = attempt_store.insert(
        iteration_id=outer_segment.id, attempt_sequence_no=1
    )
    iteration_store.append_attempt_id(outer_segment.id, outer_attempt.id)

    parent_task_id = "outer-generator-task"
    task_store.upsert_task(
        task_id=parent_task_id,
        task_center_run_id=task_center_run_id,
        role=TaskCenterTaskRole.GENERATOR.value,
        agent_name="executor",
        context_message="execute the outer task",
        status=TaskCenterBackgroundTaskStatus.RUNNING.value,
        summaries=[],
        needs=[],
        task_center_attempt_id=outer_attempt.id,
        spawn_reason="attempt_generator",
    )
    return parent_task_id, outer_attempt.id


def test_goal_start_creates_request_segment_graph_and_marks_parent_waiting(
    goal_store, iteration_store, attempt_store, task_store, task_center_run_id, composer
) -> None:
    runtime = _build_runtime(
        goal_store, iteration_store, attempt_store, task_store, composer=composer
    )
    parent_task_id, parent_attempt_id = _seed_outer_generator_task(
        task_store=task_store,
        goal_store=goal_store,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        task_center_run_id=task_center_run_id,
    )
    coordinator = GoalStarter(runtime=runtime)

    result: StartedGoal = coordinator.start(
        prompt="solve delegated task",
        origin=GoalOrigin.task(task_id=parent_task_id),
    )

    delegated_request = goal_store.get(result.goal_id)
    initial_iteration = iteration_store.get(result.initial_iteration_id)
    initial_graph = attempt_store.get(result.initial_attempt_id)
    parent_task = task_store.get_task(parent_task_id)

    assert delegated_request is not None
    assert delegated_request.status == GoalStatus.OPEN
    assert delegated_request.origin_kind == GoalOriginKind.TASK
    assert delegated_request.requested_by_task_id == parent_task_id
    assert delegated_request.goal == "solve delegated task"
    assert initial_iteration is not None
    assert initial_iteration.goal_id == delegated_request.id
    assert initial_graph is not None
    assert initial_graph.iteration_id == initial_iteration.id
    assert parent_task is not None
    assert parent_task["status"] == TaskCenterBackgroundTaskStatus.WAITING_GOAL.value
    # Delegated orchestrator was started.
    assert runtime.orchestrator_registry.get(initial_graph.id) is not None


def test_goal_start_startup_failure_leaves_parent_running(
    goal_store, iteration_store, attempt_store, task_store, task_center_run_id, composer
) -> None:
    runtime = _build_runtime(
        goal_store, iteration_store, attempt_store, task_store, composer=composer
    )
    parent_task_id, parent_attempt_id = _seed_outer_generator_task(
        task_store=task_store,
        goal_store=goal_store,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        task_center_run_id=task_center_run_id,
    )

    def _failing_factory(attempt, on_attempt_closed):
        del attempt, on_attempt_closed
        raise RuntimeError("delegated startup boom")

    starter = GoalStarter(runtime=runtime, orchestrator_factory=_failing_factory)
    with pytest.raises(RuntimeError):
        starter.start(
            prompt="delegated",
            origin=GoalOrigin.task(task_id=parent_task_id),
        )

    parent_task = task_store.get_task(parent_task_id)
    assert parent_task is not None
    assert parent_task["status"] == TaskCenterBackgroundTaskStatus.RUNNING.value
    # The compensation path must mark the request and iteration cancelled.
    open_requests = [
        r
        for r in goal_store.list_for_requesting_task(parent_task_id)
        if r.is_open
    ]
    assert open_requests == []
    cancelled = [
        r
        for r in goal_store.list_for_requesting_task(parent_task_id)
        if r.status == GoalStatus.CANCELLED
    ]
    assert len(cancelled) == 1
    assert cancelled[0].requested_by_task_id == parent_task_id
    cancelled_segment = iteration_store.list_for_goal(cancelled[0].id)
    assert len(cancelled_segment) == 1
    assert cancelled_segment[0].status == IterationStatus.CANCELLED
    assert runtime.iteration_coordinators is not None
    assert runtime.iteration_coordinators.get(cancelled_segment[0].id) is None


def test_goal_start_startup_failure_closes_started_graph_and_deregisters_orchestrator(
    goal_store, iteration_store, attempt_store, task_store, task_center_run_id, composer
) -> None:
    runtime = _build_runtime(
        goal_store,
        iteration_store,
        attempt_store,
        task_store,
        launcher=_FailingLauncher(),
        composer=composer,
    )
    parent_task_id, parent_attempt_id = _seed_outer_generator_task(
        task_store=task_store,
        goal_store=goal_store,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        task_center_run_id=task_center_run_id,
    )
    coordinator = GoalStarter(runtime=runtime)

    with pytest.raises(RuntimeError):
        coordinator.start(
            prompt="delegated",
            origin=GoalOrigin.task(task_id=parent_task_id),
        )

    [cancelled_request] = [
        r
        for r in goal_store.list_for_requesting_task(parent_task_id)
        if r.status == GoalStatus.CANCELLED
    ]
    [cancelled_segment] = iteration_store.list_for_goal(cancelled_request.id)
    [failed_attempt] = attempt_store.list_for_iteration(cancelled_segment.id)
    assert failed_attempt.status == AttemptStatus.FAILED
    assert failed_attempt.fail_reason == AttemptFailReason.STARTUP_FAILED
    assert runtime.orchestrator_registry.get(failed_attempt.id) is None
    assert runtime.iteration_coordinators is not None
    assert runtime.iteration_coordinators.get(cancelled_segment.id) is None
    planner_task = task_store.get_task(planner_task_id(failed_attempt.id))
    assert planner_task is not None
    assert planner_task["status"] == TaskCenterBackgroundTaskStatus.FAILED.value


def test_goal_start_rejects_second_open_child_request_for_same_executor(
    goal_store, iteration_store, attempt_store, task_store, task_center_run_id, composer
) -> None:
    runtime = _build_runtime(
        goal_store, iteration_store, attempt_store, task_store, composer=composer
    )
    parent_task_id, parent_attempt_id = _seed_outer_generator_task(
        task_store=task_store,
        goal_store=goal_store,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        task_center_run_id=task_center_run_id,
    )
    coordinator = GoalStarter(runtime=runtime)
    coordinator.start(
        prompt="first delegation",
        origin=GoalOrigin.task(task_id=parent_task_id),
    )

    # Restore the parent to running so the second call passes the running gate
    # but is rejected by the duplicate-open-request check.
    task_store.set_task_status(
        parent_task_id,
        status=TaskCenterBackgroundTaskStatus.RUNNING.value,
    )

    with pytest.raises(TaskCenterInvariantViolation) as exc:
        coordinator.start(
            prompt="second delegation",
            origin=GoalOrigin.task(task_id=parent_task_id),
        )
    assert "open delegated goal" in str(exc.value)


def test_goal_start_rejects_non_running_parent(
    goal_store, iteration_store, attempt_store, task_store, task_center_run_id, composer
) -> None:
    runtime = _build_runtime(
        goal_store, iteration_store, attempt_store, task_store, composer=composer
    )
    parent_task_id, parent_attempt_id = _seed_outer_generator_task(
        task_store=task_store,
        goal_store=goal_store,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        task_center_run_id=task_center_run_id,
    )
    task_store.set_task_status(
        parent_task_id, status=TaskCenterBackgroundTaskStatus.DONE.value
    )

    coordinator = GoalStarter(runtime=runtime)
    with pytest.raises(TaskCenterInvariantViolation) as exc:
        coordinator.start(
            prompt="delegated",
            origin=GoalOrigin.task(task_id=parent_task_id),
        )
    assert "not running" in str(exc.value)


def test_goal_start_accepts_entry_origin_without_parent_task(
    goal_store, iteration_store, attempt_store, task_store, task_center_run_id, composer
) -> None:
    iteration_coordinators = OpenIterationCoordinatorRegistry()
    runtime = AttemptDeps(
        goal_store=goal_store,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        task_store=task_store,
        agent_launcher=_FakeLauncher(),
        orchestrator_registry=AttemptOrchestratorRegistry(),
        iteration_coordinators=iteration_coordinators,
        composer=composer,
    )

    coordinator = GoalStarter(runtime=runtime)
    result: StartedGoal = coordinator.start(
        prompt="solve entry prompt",
        origin=GoalOrigin.entry(task_center_run_id=task_center_run_id),
    )

    assert result.origin.kind == GoalOriginKind.ENTRY
    assert result.parent_task_id is None
    assert result.parent_attempt_id is None
    delegated_request = goal_store.get(result.goal_id)
    delegated_segment = iteration_store.get(result.initial_iteration_id)
    delegated_attempt = attempt_store.get(result.initial_attempt_id)
    assert delegated_request is not None
    assert delegated_request.origin_kind == GoalOriginKind.ENTRY
    assert delegated_request.requested_by_task_id is None
    assert delegated_request.goal == "solve entry prompt"
    assert delegated_request.status == GoalStatus.OPEN
    assert delegated_segment is not None
    assert delegated_attempt is not None
    assert runtime.orchestrator_registry.get(delegated_attempt.id) is not None
