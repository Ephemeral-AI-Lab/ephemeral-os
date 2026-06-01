"""WorkflowLifecycle lifecycle tests.

A workflow's close routes through ``run_close_handler`` (the root workflow,
whose parent is the synthetic ``<run_id>:root`` bootstrap task) or through the
spawning attempt's orchestrator (a child workflow). Iteration close is a
primitive keyword signal — there is no ``IterationClosureReport`` /
``WorkflowOrigin`` / closure-report sink.
"""

from __future__ import annotations

import pytest

from workflow._core.primitives import (
    TaskCenterInvariantViolation,
    TaskCenterLifecycleConfig,
    root_task_id,
)
from workflow.lifecycle import WorkflowLifecycle
from workflow.iteration import OpenIterationCoordinatorRegistry
from workflow.attempt.orchestrator_registry import AttemptOrchestratorRegistry
from workflow._core.state import (
    IterationCreationReason,
    IterationStatus,
    WorkflowStatus,
)


@pytest.fixture
def iteration_coordinators():
    return OpenIterationCoordinatorRegistry()


@pytest.fixture
def root_closes():
    return []


@pytest.fixture
def workflow_lifecycle(
    workflow_store, iteration_store, attempt_store, iteration_coordinators, root_closes
):
    def run_close_handler(*, child_workflow):
        root_closes.append(child_workflow)

    return WorkflowLifecycle(
        workflow_store=workflow_store,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        iteration_coordinators=iteration_coordinators,
        config=TaskCenterLifecycleConfig(default_attempt_budget=2),
        orchestrator_registry=AttemptOrchestratorRegistry(),
        run_close_handler=run_close_handler,
    )


def _root_parent(task_center_run_id: str) -> str:
    """A parent task id that resolves to the root close branch."""
    return root_task_id(task_center_run_id)


def test_create_workflow_links_parent_task(
    workflow_lifecycle, workflow_store, task_center_run_id
):
    workflow = workflow_lifecycle.create_workflow(
        task_center_run_id=task_center_run_id,
        parent_task_id="executor-1",
        workflow_goal="solve X",
    )
    assert workflow.parent_task_id == "executor-1"
    assert workflow.task_center_run_id == task_center_run_id
    assert workflow.is_open
    assert workflow.iteration_ids == ()
    persisted = workflow_store.get(workflow.id)
    assert persisted is not None
    assert persisted.parent_task_id == "executor-1"


def test_workflow_records_iterations_in_iteration_ids(
    workflow_lifecycle, workflow_store, task_center_run_id
):
    workflow = workflow_lifecycle.create_workflow(
        task_center_run_id=task_center_run_id,
        parent_task_id="t1",
        workflow_goal="g",
    )
    iteration, _ = workflow_lifecycle.create_iteration_with_coordinator(
        workflow_id=workflow.id
    )
    refreshed = workflow_store.get(workflow.id)
    assert refreshed is not None
    assert refreshed.iteration_ids == (iteration.id,)


def test_initial_iteration_has_sequence_one_and_initial_reason(
    workflow_lifecycle, task_center_run_id
):
    workflow = workflow_lifecycle.create_workflow(
        task_center_run_id=task_center_run_id,
        parent_task_id="t1",
        workflow_goal="g",
    )
    iteration, _ = workflow_lifecycle.create_iteration_with_coordinator(
        workflow_id=workflow.id
    )
    assert iteration.sequence_no == 1
    assert iteration.creation_reason == IterationCreationReason.INITIAL
    assert iteration.iteration_goal == "g"
    assert iteration.is_open
    assert iteration.attempt_budget == 2


def test_continuation_iteration_inherits_deferred_goal(
    workflow_lifecycle, iteration_store, task_center_run_id
):
    workflow = workflow_lifecycle.create_workflow(
        task_center_run_id=task_center_run_id,
        parent_task_id="t1",
        workflow_goal="initial-goal",
    )
    iteration1, _ = workflow_lifecycle.create_iteration_with_coordinator(
        workflow_id=workflow.id
    )
    iteration_store.set_deferred_goal_for_next_iteration(
        iteration1.id, deferred_goal_for_next_iteration="next-goal"
    )
    iteration_store.set_status(iteration1.id, status=IterationStatus.SUCCEEDED)
    iteration1_succeeded = iteration_store.get(iteration1.id)
    assert iteration1_succeeded is not None

    iteration2, _ = workflow_lifecycle.create_iteration_with_coordinator(
        workflow_id=iteration1_succeeded.workflow_id
    )
    assert iteration2.sequence_no == 2
    assert iteration2.creation_reason == IterationCreationReason.DEFERRED_GOAL_CONTINUATION
    assert iteration2.iteration_goal == "next-goal"


def test_iteration_ids_holds_multiple_iterations(
    workflow_lifecycle, workflow_store, iteration_store, task_center_run_id
):
    workflow = workflow_lifecycle.create_workflow(
        task_center_run_id=task_center_run_id,
        parent_task_id="t1",
        workflow_goal="g1",
    )
    iteration1, _ = workflow_lifecycle.create_iteration_with_coordinator(
        workflow_id=workflow.id
    )
    iteration_store.set_deferred_goal_for_next_iteration(
        iteration1.id, deferred_goal_for_next_iteration="g2"
    )
    iteration_store.set_status(iteration1.id, status=IterationStatus.SUCCEEDED)
    iteration1_succeeded = iteration_store.get(iteration1.id)
    assert iteration1_succeeded is not None
    iteration2, _ = workflow_lifecycle.create_iteration_with_coordinator(
        workflow_id=iteration1_succeeded.workflow_id
    )
    refreshed = workflow_store.get(workflow.id)
    assert refreshed is not None
    assert refreshed.iteration_ids == (iteration1.id, iteration2.id)


def test_handle_iteration_closed_success_closes_workflow_succeeded(
    workflow_lifecycle, workflow_store, root_closes, task_center_run_id
):
    workflow = workflow_lifecycle.create_workflow(
        task_center_run_id=task_center_run_id,
        parent_task_id=_root_parent(task_center_run_id),
        workflow_goal="g",
    )
    iteration, _ = workflow_lifecycle.create_iteration_with_coordinator(
        workflow_id=workflow.id
    )
    workflow_lifecycle.handle_iteration_closed(
        iteration_id=iteration.id,
        succeeded=True,
        deferred_goal=None,    )
    final = workflow_store.get(workflow.id)
    assert final is not None
    assert final.status == WorkflowStatus.SUCCEEDED
    assert root_closes == [final]


def test_handle_iteration_closed_failure_closes_workflow_failed(
    workflow_lifecycle, workflow_store, root_closes, task_center_run_id
):
    workflow = workflow_lifecycle.create_workflow(
        task_center_run_id=task_center_run_id,
        parent_task_id=_root_parent(task_center_run_id),
        workflow_goal="g",
    )
    iteration, _ = workflow_lifecycle.create_iteration_with_coordinator(
        workflow_id=workflow.id
    )
    workflow_lifecycle.handle_iteration_closed(
        iteration_id=iteration.id,
        succeeded=False,
        deferred_goal=None,    )
    final = workflow_store.get(workflow.id)
    assert final is not None
    assert final.status == WorkflowStatus.FAILED
    assert root_closes == [final]


def test_handle_iteration_closed_success_continue_creates_continuation(
    workflow_lifecycle, workflow_store, iteration_store, task_center_run_id
):
    workflow = workflow_lifecycle.create_workflow(
        task_center_run_id=task_center_run_id,
        parent_task_id=_root_parent(task_center_run_id),
        workflow_goal="g",
    )
    iteration1, _ = workflow_lifecycle.create_iteration_with_coordinator(
        workflow_id=workflow.id
    )
    iteration_store.set_deferred_goal_for_next_iteration(
        iteration1.id, deferred_goal_for_next_iteration="next-goal"
    )
    iteration_store.set_status(iteration1.id, status=IterationStatus.SUCCEEDED)
    workflow_lifecycle.handle_iteration_closed(
        iteration_id=iteration1.id,
        succeeded=True,
        deferred_goal="next-goal",    )
    refreshed = workflow_store.get(workflow.id)
    assert refreshed is not None
    assert len(refreshed.iteration_ids) == 2
    iteration2_id = refreshed.iteration_ids[1]
    iteration2 = iteration_store.get(iteration2_id)
    assert iteration2 is not None
    assert iteration2.sequence_no == 2
    assert iteration2.iteration_goal == "next-goal"


def test_handle_iteration_closed_deregisters_coordinator(
    workflow_lifecycle, iteration_coordinators, task_center_run_id
):
    workflow = workflow_lifecycle.create_workflow(
        task_center_run_id=task_center_run_id,
        parent_task_id=_root_parent(task_center_run_id),
        workflow_goal="g",
    )
    iteration, _ = workflow_lifecycle.create_iteration_with_coordinator(
        workflow_id=workflow.id
    )
    assert iteration_coordinators.get(iteration.id) is not None
    workflow_lifecycle.handle_iteration_closed(
        iteration_id=iteration.id,
        succeeded=True,
        deferred_goal=None,    )
    assert iteration_coordinators.get(iteration.id) is None


def test_continuation_iteration_only_from_succeeded_predecessor_with_goal(
    workflow_lifecycle, iteration_store, task_center_run_id
):
    workflow = workflow_lifecycle.create_workflow(
        task_center_run_id=task_center_run_id,
        parent_task_id="t1",
        workflow_goal="g",
    )
    iteration1, _ = workflow_lifecycle.create_iteration_with_coordinator(
        workflow_id=workflow.id
    )

    # Predecessor still OPEN -> invariant violation.
    with pytest.raises(TaskCenterInvariantViolation):
        workflow_lifecycle.create_iteration_with_coordinator(
            workflow_id=iteration1.workflow_id
        )

    # Predecessor SUCCEEDED but no deferred_goal_for_next_iteration -> violation.
    iteration_store.set_status(iteration1.id, status=IterationStatus.SUCCEEDED)
    iteration1_no_goal = iteration_store.get(iteration1.id)
    assert iteration1_no_goal is not None
    with pytest.raises(TaskCenterInvariantViolation):
        workflow_lifecycle.create_iteration_with_coordinator(
            workflow_id=iteration1_no_goal.workflow_id
        )


def test_open_iteration_coordinators_enforces_unique_per_iteration(
    workflow_lifecycle, task_center_run_id
):
    """Exactly one IterationAttemptCoordinator active per open iteration."""
    workflow = workflow_lifecycle.create_workflow(
        task_center_run_id=task_center_run_id,
        parent_task_id="t1",
        workflow_goal="g",
    )
    workflow_lifecycle.create_iteration_with_coordinator(workflow_id=workflow.id)
    # Calling it again must fail: the only iteration is still OPEN, so it cannot
    # serve as a SUCCEEDED predecessor for a continuation iteration.
    with pytest.raises(TaskCenterInvariantViolation):
        workflow_lifecycle.create_iteration_with_coordinator(workflow_id=workflow.id)


def test_close_workflow_routes_to_run_close_handler_for_root(
    workflow_store, iteration_store, attempt_store, root_closes, task_center_run_id
):
    delivered: list = []

    def run_close_handler(*, child_workflow):
        delivered.append(child_workflow)

    workflow_lifecycle = WorkflowLifecycle(
        workflow_store=workflow_store,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        iteration_coordinators=OpenIterationCoordinatorRegistry(),
        config=TaskCenterLifecycleConfig(default_attempt_budget=2),
        orchestrator_registry=AttemptOrchestratorRegistry(),
        run_close_handler=run_close_handler,
    )
    workflow = workflow_lifecycle.create_workflow(
        task_center_run_id=task_center_run_id,
        parent_task_id=_root_parent(task_center_run_id),
        workflow_goal="g",
    )
    workflow_lifecycle.create_iteration_with_coordinator(workflow_id=workflow.id)
    closed = workflow_lifecycle.close_workflow(
        workflow_id=workflow.id,
        succeeded=True,    )
    assert closed.status == WorkflowStatus.SUCCEEDED
    assert delivered == [closed]


def test_workflow_lifecycle_passes_orchestrator_factory_to_spawned_coordinator(
    workflow_store, iteration_store, attempt_store, task_center_run_id
):
    started: list[str] = []

    class _StartedOrchestrator:
        def __init__(self, attempt_id: str) -> None:
            self.attempt_id = attempt_id

        def start(self) -> None:
            started.append(self.attempt_id)

    def factory(attempt, on_attempt_closed):
        del on_attempt_closed
        return _StartedOrchestrator(attempt.id)

    registry = OpenIterationCoordinatorRegistry()
    workflow_lifecycle = WorkflowLifecycle(
        workflow_store=workflow_store,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        iteration_coordinators=registry,
        config=TaskCenterLifecycleConfig(default_attempt_budget=2),
        orchestrator_registry=AttemptOrchestratorRegistry(),
        run_close_handler=lambda **_: None,
        orchestrator_factory=factory,
    )
    workflow = workflow_lifecycle.create_workflow(
        task_center_run_id=task_center_run_id,
        parent_task_id="executor-1",
        workflow_goal="g",
    )
    iteration, _ = workflow_lifecycle.create_iteration_with_coordinator(
        workflow_id=workflow.id
    )
    coordinator = registry.get(iteration.id)
    assert coordinator is not None

    attempt = coordinator.create_attempt()

    assert started == [attempt.id]


def test_no_legacy_entry_creation_reason_in_lifecycle(
    workflow_lifecycle, task_center_run_id
):
    """No special entry creation reason is allowed: only INITIAL or
    DEFERRED_GOAL_CONTINUATION."""
    workflow = workflow_lifecycle.create_workflow(
        task_center_run_id=task_center_run_id,
        parent_task_id="t1",
        workflow_goal="g",
    )
    iteration, _ = workflow_lifecycle.create_iteration_with_coordinator(
        workflow_id=workflow.id
    )
    assert iteration.creation_reason in (
        IterationCreationReason.INITIAL,
        IterationCreationReason.DEFERRED_GOAL_CONTINUATION,
    )
