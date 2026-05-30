"""Workflow request starter tests.

Covers happy path, startup-failure rollback, duplicate-open-child gating, and
the non-running-parent guard. ``WorkflowStarter.start`` takes a concrete running
``parent_task_id`` (an attempt-bound generator) — there is no origin
abstraction; the root path lives in ``RunController`` instead.
"""

from __future__ import annotations

import pytest

from task_center.workflow.starter import (
    StartedWorkflow,
    WorkflowStarter,
)
from task_center._core.primitives import (
    TaskCenterInvariantViolation,
    generator_task_id,
    planner_task_id,
)
from task_center._core.state import (
    AttemptFailReason,
    AttemptStatus,
    IterationCreationReason,
    IterationStatus,
    WorkflowStatus,
)
from task_center.attempt.launch import AgentLaunch, AttemptDeps
from task_center.attempt.orchestrator import AttemptOrchestrator
from task_center.attempt.orchestrator_registry import (
    AttemptOrchestratorRegistry,
)
from task_center.iteration import OpenIterationCoordinatorRegistry
from task_center._core.task_state import TaskCenterTaskStatus
from task_center.submissions import (
    PlannedGeneratorTask,
    PlannedReducerTask,
    PlannerSubmission,
)


class _FakeLauncher:
    def __init__(self) -> None:
        self.launches: list[AgentLaunch] = []

    def launch(self, launch: AgentLaunch) -> None:
        self.launches.append(launch)


class _FailOnLaunchNumber(_FakeLauncher):
    """Records launches but raises on the ``fail_on``-th one."""

    def __init__(self, fail_on: int) -> None:
        super().__init__()
        self._fail_on = fail_on

    def launch(self, launch: AgentLaunch) -> None:
        super().launch(launch)
        if len(self.launches) == self._fail_on:
            raise RuntimeError("delegated planner launch boom")


def _build_runtime(
    workflow_store, iteration_store, attempt_store, task_store, *, composer, launcher=None
) -> AttemptDeps:
    return AttemptDeps(
        workflow_store=workflow_store,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        task_store=task_store,
        agent_launcher=launcher or _FakeLauncher(),
        orchestrator_registry=AttemptOrchestratorRegistry(),
        iteration_coordinators=OpenIterationCoordinatorRegistry(),
        composer=composer,
    )


def _seed_outer_running_generator(
    *, runtime: AttemptDeps, task_center_run_id: str
) -> tuple[str, str]:
    """Seed an outer parent attempt whose generator ``outer`` is RUNNING."""
    outer_workflow = runtime.workflow_store.insert(
        task_center_run_id=task_center_run_id,
        parent_task_id=f"{task_center_run_id}:root",
        workflow_goal="outer goal",
    )
    outer_iteration = runtime.iteration_store.insert(
        workflow_id=outer_workflow.id,
        sequence_no=1,
        creation_reason=IterationCreationReason.INITIAL,
        iteration_goal="outer goal",
        attempt_budget=2,
    )
    runtime.workflow_store.append_iteration_id(outer_workflow.id, outer_iteration.id)
    outer_attempt = runtime.attempt_store.insert(
        iteration_id=outer_iteration.id, attempt_sequence_no=1
    )
    runtime.iteration_store.append_attempt_id(outer_iteration.id, outer_attempt.id)
    orchestrator = AttemptOrchestrator(
        attempt=outer_attempt, on_attempt_closed=lambda _id: None, runtime=runtime
    )
    runtime.orchestrator_registry.register(orchestrator)
    orchestrator.start()
    orchestrator.apply_plan_submission(
        PlannerSubmission(
            attempt_id=outer_attempt.id,
            planner_task_id=planner_task_id(outer_attempt.id),
            kind="completes",
            tasks=(
                PlannedGeneratorTask(
                    local_id="outer", agent_name="executor", needs=(), task_spec="execute outer"
                ),
            ),
            reducers=(PlannedReducerTask(local_id="r", needs=("outer",), prompt="gate"),),
            deferred_goal_for_next_iteration=None,
            outcome="outer plan",
        )
    )
    parent_task_id = generator_task_id(outer_attempt.id, "outer")
    return parent_task_id, outer_attempt.id


def test_workflow_start_creates_request_iteration_attempt_and_marks_parent_waiting(
    workflow_store, iteration_store, attempt_store, task_store, task_center_run_id, composer
) -> None:
    runtime = _build_runtime(
        workflow_store, iteration_store, attempt_store, task_store, composer=composer
    )
    parent_task_id, parent_attempt_id = _seed_outer_running_generator(
        runtime=runtime, task_center_run_id=task_center_run_id
    )
    starter = WorkflowStarter(runtime=runtime)

    result: StartedWorkflow = starter.start(
        prompt="solve delegated task", parent_task_id=parent_task_id
    )

    delegated = workflow_store.get(result.workflow_id)
    initial_iteration = iteration_store.get(result.iteration_id)
    initial_attempt = attempt_store.get(result.attempt_id)
    parent_task = task_store.get_task(parent_task_id)

    assert result.parent_task_id == parent_task_id
    assert result.parent_attempt_id == parent_attempt_id
    assert delegated is not None
    assert delegated.status == WorkflowStatus.OPEN
    assert delegated.parent_task_id == parent_task_id
    assert delegated.workflow_goal == "solve delegated task"
    assert initial_iteration is not None
    assert initial_iteration.workflow_id == delegated.id
    assert initial_attempt is not None
    assert initial_attempt.iteration_id == initial_iteration.id
    assert parent_task is not None
    assert parent_task["status"] == TaskCenterTaskStatus.WAITING_WORKFLOW.value
    assert parent_task["child_workflow_id"] == delegated.id
    assert runtime.orchestrator_registry.get(initial_attempt.id) is not None


def test_workflow_start_startup_failure_leaves_parent_running(
    workflow_store, iteration_store, attempt_store, task_store, task_center_run_id, composer
) -> None:
    runtime = _build_runtime(
        workflow_store, iteration_store, attempt_store, task_store, composer=composer
    )
    parent_task_id, parent_attempt_id = _seed_outer_running_generator(
        runtime=runtime, task_center_run_id=task_center_run_id
    )

    def _failing_factory(attempt, on_attempt_closed):
        del attempt, on_attempt_closed
        raise RuntimeError("delegated startup boom")

    starter = WorkflowStarter(runtime=runtime, orchestrator_factory=_failing_factory)
    with pytest.raises(RuntimeError):
        starter.start(prompt="delegated", parent_task_id=parent_task_id)

    parent_task = task_store.get_task(parent_task_id)
    assert parent_task is not None
    assert parent_task["status"] == TaskCenterTaskStatus.RUNNING.value
    open_requests = [
        r for r in workflow_store.list_for_parent_task(parent_task_id) if r.is_open
    ]
    assert open_requests == []
    cancelled = [
        r
        for r in workflow_store.list_for_parent_task(parent_task_id)
        if r.status == WorkflowStatus.CANCELLED
    ]
    assert len(cancelled) == 1
    assert cancelled[0].parent_task_id == parent_task_id
    cancelled_iteration = iteration_store.list_for_workflow(cancelled[0].id)
    assert len(cancelled_iteration) == 1
    assert cancelled_iteration[0].status == IterationStatus.CANCELLED
    assert runtime.iteration_coordinators is not None
    assert runtime.iteration_coordinators.get(cancelled_iteration[0].id) is None


def test_workflow_start_startup_failure_closes_started_attempt_and_deregisters(
    workflow_store, iteration_store, attempt_store, task_store, task_center_run_id, composer
) -> None:
    # Outer attempt uses 2 launches (planner + generator); fail on the 3rd —
    # the delegated planner launch.
    runtime = _build_runtime(
        workflow_store,
        iteration_store,
        attempt_store,
        task_store,
        launcher=_FailOnLaunchNumber(fail_on=3),
        composer=composer,
    )
    parent_task_id, parent_attempt_id = _seed_outer_running_generator(
        runtime=runtime, task_center_run_id=task_center_run_id
    )
    starter = WorkflowStarter(runtime=runtime)

    with pytest.raises(RuntimeError):
        starter.start(prompt="delegated", parent_task_id=parent_task_id)

    [cancelled_request] = [
        r
        for r in workflow_store.list_for_parent_task(parent_task_id)
        if r.status == WorkflowStatus.CANCELLED
    ]
    [cancelled_iteration] = iteration_store.list_for_workflow(cancelled_request.id)
    [failed_attempt] = attempt_store.list_for_iteration(cancelled_iteration.id)
    assert failed_attempt.status == AttemptStatus.FAILED
    assert failed_attempt.fail_reason == AttemptFailReason.STARTUP_FAILED
    assert runtime.orchestrator_registry.get(failed_attempt.id) is None
    assert runtime.iteration_coordinators is not None
    assert runtime.iteration_coordinators.get(cancelled_iteration.id) is None
    planner_task = task_store.get_task(planner_task_id(failed_attempt.id))
    assert planner_task is not None
    assert planner_task["status"] == TaskCenterTaskStatus.FAILED.value


def test_workflow_start_rejects_second_open_child_for_same_generator(
    workflow_store, iteration_store, attempt_store, task_store, task_center_run_id, composer
) -> None:
    runtime = _build_runtime(
        workflow_store, iteration_store, attempt_store, task_store, composer=composer
    )
    parent_task_id, parent_attempt_id = _seed_outer_running_generator(
        runtime=runtime, task_center_run_id=task_center_run_id
    )
    starter = WorkflowStarter(runtime=runtime)
    starter.start(prompt="first delegation", parent_task_id=parent_task_id)

    # Restore the parent to running so the second call passes the running gate
    # but is rejected by the duplicate-open-child check.
    task_store.set_task_status(
        parent_task_id, status=TaskCenterTaskStatus.RUNNING.value
    )

    with pytest.raises(TaskCenterInvariantViolation) as exc:
        starter.start(prompt="second delegation", parent_task_id=parent_task_id)
    assert "open delegated workflow" in str(exc.value)


def test_workflow_start_rejects_non_running_parent(
    workflow_store, iteration_store, attempt_store, task_store, task_center_run_id, composer
) -> None:
    runtime = _build_runtime(
        workflow_store, iteration_store, attempt_store, task_store, composer=composer
    )
    parent_task_id, parent_attempt_id = _seed_outer_running_generator(
        runtime=runtime, task_center_run_id=task_center_run_id
    )
    task_store.set_task_status(parent_task_id, status=TaskCenterTaskStatus.DONE.value)

    starter = WorkflowStarter(runtime=runtime)
    with pytest.raises(TaskCenterInvariantViolation) as exc:
        starter.start(prompt="delegated", parent_task_id=parent_task_id)
    assert "not running" in str(exc.value)
