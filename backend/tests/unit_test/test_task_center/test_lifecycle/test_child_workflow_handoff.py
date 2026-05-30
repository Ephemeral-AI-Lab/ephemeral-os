"""Child-workflow handoff resolution (replaces the removed closure router).

A generator task that delegated work via ``submit_workflow_handoff`` sits in
``waiting_workflow`` until its child workflow closes. The child workflow's
:class:`WorkflowLifecycle` then routes the close back to the spawning attempt's
orchestrator (``apply_child_workflow_outcome``) — for the root workflow it
routes through the injected run-close handler instead. These tests pin both the
success and failure branches plus the root path, all against the orchestrator +
lifecycle surface (there is no router layer anymore).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from task_center._core.primitives import (
    TaskCenterInvariantViolation,
    TaskCenterLifecycleConfig,
    generator_task_id,
    planner_task_id,
)
from task_center._core.state import Workflow, WorkflowStatus
from task_center._core.task_state import TaskCenterTaskStatus
from task_center.attempt.launch import AgentLaunch, AttemptDeps
from task_center.attempt.orchestrator import AttemptOrchestrator
from task_center.attempt.orchestrator_registry import AttemptOrchestratorRegistry
from task_center.iteration import OpenIterationCoordinatorRegistry
from task_center.submissions import (
    PlannedGeneratorTask,
    PlannedReducerTask,
    PlannerSubmission,
)
from task_center.workflow.lifecycle import WorkflowLifecycle


class _FakeLauncher:
    def __init__(self) -> None:
        self.launches: list[AgentLaunch] = []

    def launch(self, launch: AgentLaunch) -> None:
        self.launches.append(launch)


def _build_runtime(workflow_store, iteration_store, attempt_store, task_store, *, composer):
    return AttemptDeps(
        workflow_store=workflow_store,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        task_store=task_store,
        agent_launcher=_FakeLauncher(),
        orchestrator_registry=AttemptOrchestratorRegistry(),
        iteration_coordinators=OpenIterationCoordinatorRegistry(),
        composer=composer,
    )


def _seed_attempt_with_waiting_generator(
    *, runtime: AttemptDeps, task_center_run_id: str
) -> tuple[str, str]:
    """Seed a parent attempt whose generator ``a`` is waiting on a child workflow."""
    workflow = runtime.workflow_store.insert(
        task_center_run_id=task_center_run_id,
        parent_task_id=None,
        workflow_goal="outer",
    )
    from task_center._core.state import IterationCreationReason

    iteration = runtime.iteration_store.insert(
        workflow_id=workflow.id,
        sequence_no=1,
        creation_reason=IterationCreationReason.INITIAL,
        iteration_goal="outer",
        attempt_budget=2,
    )
    runtime.workflow_store.append_iteration_id(workflow.id, iteration.id)
    attempt = runtime.attempt_store.insert(iteration_id=iteration.id, attempt_sequence_no=1)
    runtime.iteration_store.append_attempt_id(iteration.id, attempt.id)
    orchestrator = AttemptOrchestrator(
        attempt=attempt, on_attempt_closed=lambda _id: None, runtime=runtime
    )
    runtime.orchestrator_registry.register(orchestrator)
    orchestrator.start()
    orchestrator.apply_plan_submission(
        PlannerSubmission(
            attempt_id=attempt.id,
            planner_task_id=planner_task_id(attempt.id),
            kind="completes",
            tasks=(
                PlannedGeneratorTask(local_id="a", agent_name="executor", needs=(), task_spec="do a"),
                PlannedGeneratorTask(
                    local_id="b", agent_name="executor", needs=("a",), task_spec="do b"
                ),
            ),
            reducers=(PlannedReducerTask(local_id="r", needs=("a", "b"), prompt="gate"),),
            deferred_goal_for_next_iteration=None,
            outcome="plan",
        )
    )
    parent_task_id = generator_task_id(attempt.id, "a")
    return parent_task_id, attempt.id


def _mark_waiting(runtime: AttemptDeps, task_id: str, child_workflow: Workflow) -> None:
    runtime.task_store.set_task_status_if_current(
        task_id,
        expected_status=TaskCenterTaskStatus.RUNNING.value,
        status=TaskCenterTaskStatus.WAITING_WORKFLOW.value,
        child_workflow_id=child_workflow.id,
    )


def _closed_child(runtime: AttemptDeps, run_id: str, *, succeeded: bool, parent_task_id: str) -> Workflow:
    child = runtime.workflow_store.insert(
        task_center_run_id=run_id,
        parent_task_id=parent_task_id,
        workflow_goal="child",
    )
    return runtime.workflow_store.set_status(
        child.id,
        status=WorkflowStatus.SUCCEEDED if succeeded else WorkflowStatus.FAILED,
        closed_at=datetime.now(UTC),
    )


def test_child_workflow_success_marks_waiting_generator_done(
    workflow_store, iteration_store, attempt_store, task_store, task_center_run_id, composer
) -> None:
    runtime = _build_runtime(
        workflow_store, iteration_store, attempt_store, task_store, composer=composer
    )
    parent_task_id, parent_attempt_id = _seed_attempt_with_waiting_generator(
        runtime=runtime, task_center_run_id=task_center_run_id
    )
    child = _closed_child(
        runtime, task_center_run_id, succeeded=True, parent_task_id=parent_task_id
    )
    _mark_waiting(runtime, parent_task_id, child)

    orchestrator = runtime.orchestrator_registry.get_or_raise(parent_attempt_id)
    orchestrator.apply_child_workflow_outcome(
        generator_task=task_store.get_task(parent_task_id),
        child_workflow=child,
        final_attempt_id=None,
    )

    parent_task = task_store.get_task(parent_task_id)
    assert parent_task is not None
    assert parent_task["status"] == TaskCenterTaskStatus.DONE.value
    assert parent_task["child_workflow_id"] == child.id


def test_child_workflow_failure_marks_waiting_generator_failed(
    workflow_store, iteration_store, attempt_store, task_store, task_center_run_id, composer
) -> None:
    runtime = _build_runtime(
        workflow_store, iteration_store, attempt_store, task_store, composer=composer
    )
    parent_task_id, parent_attempt_id = _seed_attempt_with_waiting_generator(
        runtime=runtime, task_center_run_id=task_center_run_id
    )
    dependent_id = generator_task_id(parent_attempt_id, "b")
    child = _closed_child(
        runtime, task_center_run_id, succeeded=False, parent_task_id=parent_task_id
    )
    _mark_waiting(runtime, parent_task_id, child)

    orchestrator = runtime.orchestrator_registry.get_or_raise(parent_attempt_id)
    orchestrator.apply_child_workflow_outcome(
        generator_task=task_store.get_task(parent_task_id),
        child_workflow=child,
        final_attempt_id=None,
    )

    parent_task = task_store.get_task(parent_task_id)
    dependent = task_store.get_task(dependent_id)
    assert parent_task is not None
    assert parent_task["status"] == TaskCenterTaskStatus.FAILED.value
    # The dependent generator is still pending; the failed parent blocks it.
    assert dependent is not None
    assert dependent["status"] == TaskCenterTaskStatus.PENDING.value


def test_child_workflow_outcome_is_idempotent_on_second_delivery(
    workflow_store, iteration_store, attempt_store, task_store, task_center_run_id, composer
) -> None:
    runtime = _build_runtime(
        workflow_store, iteration_store, attempt_store, task_store, composer=composer
    )
    parent_task_id, parent_attempt_id = _seed_attempt_with_waiting_generator(
        runtime=runtime, task_center_run_id=task_center_run_id
    )
    child = _closed_child(
        runtime, task_center_run_id, succeeded=True, parent_task_id=parent_task_id
    )
    _mark_waiting(runtime, parent_task_id, child)
    orchestrator = runtime.orchestrator_registry.get_or_raise(parent_attempt_id)

    orchestrator.apply_child_workflow_outcome(
        generator_task=task_store.get_task(parent_task_id),
        child_workflow=child,
        final_attempt_id=None,
    )
    # Second delivery: parent already moved off waiting_workflow → silent no-op.
    orchestrator.apply_child_workflow_outcome(
        generator_task=task_store.get_task(parent_task_id),
        child_workflow=child,
        final_attempt_id=None,
    )

    parent_task = task_store.get_task(parent_task_id)
    assert parent_task is not None
    assert parent_task["status"] == TaskCenterTaskStatus.DONE.value
    assert len(parent_task["outcomes"]) == 1


def test_root_workflow_close_routes_through_run_close_handler(
    workflow_store, iteration_store, attempt_store, task_store, task_center_run_id, composer
) -> None:
    """The root workflow has no attempt parent; its close hits the run handler."""
    runtime = _build_runtime(
        workflow_store, iteration_store, attempt_store, task_store, composer=composer
    )
    root_parent = f"{task_center_run_id}:root"
    root_workflow = workflow_store.insert(
        task_center_run_id=task_center_run_id,
        parent_task_id=root_parent,
        workflow_goal="root",
    )

    handled: list[Workflow] = []

    lifecycle = WorkflowLifecycle(
        workflow_store=workflow_store,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        iteration_coordinators=OpenIterationCoordinatorRegistry(),
        config=TaskCenterLifecycleConfig(),
        orchestrator_registry=runtime.orchestrator_registry,
        run_close_handler=lambda *, child_workflow: handled.append(child_workflow),
        task_store=task_store,
    )

    closed = lifecycle.close_workflow(
        workflow_id=root_workflow.id, succeeded=True, final_attempt_id=None
    )

    assert closed.status == WorkflowStatus.SUCCEEDED
    assert [w.id for w in handled] == [root_workflow.id]


def test_child_workflow_close_raises_when_parent_orchestrator_missing(
    workflow_store, iteration_store, attempt_store, task_store, task_center_run_id, composer
) -> None:
    """No-restart invariant: a child-workflow close whose parent attempt has no
    registered orchestrator is a hard ``TaskCenterInvariantViolation``."""
    runtime = _build_runtime(
        workflow_store, iteration_store, attempt_store, task_store, composer=composer
    )
    parent_task_id, parent_attempt_id = _seed_attempt_with_waiting_generator(
        runtime=runtime, task_center_run_id=task_center_run_id
    )
    child = _closed_child(
        runtime, task_center_run_id, succeeded=True, parent_task_id=parent_task_id
    )
    _mark_waiting(runtime, parent_task_id, child)
    runtime.orchestrator_registry.deregister(parent_attempt_id)

    lifecycle = WorkflowLifecycle(
        workflow_store=workflow_store,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        iteration_coordinators=OpenIterationCoordinatorRegistry(),
        config=TaskCenterLifecycleConfig(),
        orchestrator_registry=runtime.orchestrator_registry,
        run_close_handler=lambda *, child_workflow: None,
        task_store=task_store,
    )

    with pytest.raises(TaskCenterInvariantViolation):
        # The child is already closed; routing alone must raise on the missing
        # orchestrator.
        lifecycle._route_close(child, final_attempt_id=None)

    parent_task = task_store.get_task(parent_task_id)
    assert parent_task is not None
    assert parent_task["status"] == TaskCenterTaskStatus.WAITING_WORKFLOW.value
