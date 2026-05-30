"""Workflow lifecycle closure smoke with a synchronous attempt closer.

A stub orchestrator closes its attempt immediately with a caller-supplied
verdict and signals the coordinator; the coordinator denormalizes outcomes,
closes the iteration, and routes the workflow close through
``WorkflowLifecycle`` (here every workflow is a root workflow, so the close
lands on the injected run-close handler).
"""

from __future__ import annotations

from collections.abc import Callable

from db.stores.attempt_store import AttemptStore
from task_center._core.primitives import (
    TaskCenterLifecycleConfig,
    root_task_id,
)
from task_center._core.state import (
    Attempt,
    AttemptFailReason,
    AttemptStatus,
    Workflow,
    WorkflowStatus,
    IterationStatus,
)
from task_center._core.task_state import TaskCenterTaskRole, TaskCenterTaskStatus
from task_center.workflow.lifecycle import WorkflowLifecycle
from task_center.iteration import (
    IterationAttemptCoordinator,
    OpenIterationCoordinatorRegistry,
)
from task_center.attempt.orchestrator_registry import AttemptOrchestratorRegistry


class _StubOrchestrator:
    """Synchronous stand-in for AttemptOrchestrator.

    Closes the attempt immediately on ``start`` with a caller-supplied verdict.
    """

    def __init__(
        self,
        *,
        attempt: Attempt,
        attempt_store: AttemptStore,
        on_attempt_closed: Callable[[str], None],
        verdict: tuple[AttemptStatus, AttemptFailReason | None, str | None],
    ) -> None:
        self._g = attempt
        self._gs = attempt_store
        self._cb = on_attempt_closed
        self._verdict = verdict

    def start(self) -> None:
        status, fail_reason, deferred_goal = self._verdict
        if deferred_goal is not None:
            self._gs.set_deferred_goal(
                self._g.id, deferred_goal_for_next_iteration=deferred_goal
            )
        self._gs.close(self._g.id, status=status, fail_reason=fail_reason)
        self._cb(self._g.id)


def _build(workflow_store, iteration_store, attempt_store, task_store):
    iteration_coordinators = OpenIterationCoordinatorRegistry()
    closed_workflows: list[Workflow] = []
    workflow_lifecycle = WorkflowLifecycle(
        workflow_store=workflow_store,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        iteration_coordinators=iteration_coordinators,
        config=TaskCenterLifecycleConfig(default_attempt_budget=2),
        orchestrator_registry=AttemptOrchestratorRegistry(),
        run_close_handler=lambda *, child_workflow: closed_workflows.append(child_workflow),
        task_store=task_store,
    )
    return workflow_lifecycle, iteration_coordinators, closed_workflows


def _root_workflow(workflow_lifecycle, task_store, run_id, *, goal):
    task_store.upsert_task(
        task_id=root_task_id(run_id),
        task_center_run_id=run_id,
        role=TaskCenterTaskRole.GENERATOR.value,
        agent_name=None,
        context_message="",
        status=TaskCenterTaskStatus.RUNNING.value,
        outcomes=[],
        needs=[],
    )
    return workflow_lifecycle.create_workflow(
        task_center_run_id=run_id, parent_task_id=root_task_id(run_id), workflow_goal=goal
    )


def _drive_iteration(
    *,
    iteration_coordinators: OpenIterationCoordinatorRegistry,
    iteration_id: str,
    attempt_store: AttemptStore,
    verdict: tuple[AttemptStatus, AttemptFailReason | None, str | None],
) -> None:
    coordinator: IterationAttemptCoordinator | None = iteration_coordinators.get(iteration_id)
    assert coordinator is not None
    attempt = coordinator.create_attempt()
    stub = _StubOrchestrator(
        attempt=attempt,
        attempt_store=attempt_store,
        on_attempt_closed=coordinator.handle_attempt_closed,
        verdict=verdict,
    )
    stub.start()


def test_smoke_terminal_success(
    workflow_store, iteration_store, attempt_store, task_store, task_center_run_id
):
    workflow_lifecycle, iteration_coordinators, closed = _build(
        workflow_store, iteration_store, attempt_store, task_store
    )
    workflow = _root_workflow(
        workflow_lifecycle, task_store, task_center_run_id, goal="solve X"
    )
    iteration, _ = workflow_lifecycle.create_iteration_with_coordinator(
        workflow_id=workflow.id
    )
    _drive_iteration(
        iteration_coordinators=iteration_coordinators,
        iteration_id=iteration.id,
        attempt_store=attempt_store,
        verdict=(AttemptStatus.PASSED, None, None),
    )
    final_workflow = workflow_store.get(workflow.id)
    final_iteration = iteration_store.get(iteration.id)
    assert final_workflow is not None and final_iteration is not None
    assert final_workflow.status == WorkflowStatus.SUCCEEDED
    assert final_iteration.status == IterationStatus.SUCCEEDED
    assert [w.id for w in closed] == [workflow.id]


def test_smoke_attempt_failed_exhausts_budget(
    workflow_store, iteration_store, attempt_store, task_store, task_center_run_id
):
    workflow_lifecycle, iteration_coordinators, closed = _build(
        workflow_store, iteration_store, attempt_store, task_store
    )
    workflow = _root_workflow(
        workflow_lifecycle, task_store, task_center_run_id, goal="solve X"
    )
    iteration, _ = workflow_lifecycle.create_iteration_with_coordinator(
        workflow_id=workflow.id
    )
    coordinator = iteration_coordinators.get(iteration.id)
    assert coordinator is not None
    # First attempt fails -> coordinator retries inside the same iteration.
    a1 = coordinator.create_attempt()
    attempt_store.close(
        a1.id, status=AttemptStatus.FAILED, fail_reason=AttemptFailReason.TASK_FAILED
    )
    coordinator.handle_attempt_closed(a1.id)
    # Second (budget-final) attempt also fails -> iteration + workflow fail.
    iteration_after = iteration_store.get(iteration.id)
    assert iteration_after is not None
    a2_id = iteration_after.attempt_ids[-1]
    attempt_store.close(
        a2_id, status=AttemptStatus.FAILED, fail_reason=AttemptFailReason.TASK_FAILED
    )
    coordinator.handle_attempt_closed(a2_id)

    final_workflow = workflow_store.get(workflow.id)
    final_iteration = iteration_store.get(iteration.id)
    assert final_workflow is not None and final_iteration is not None
    assert final_workflow.status == WorkflowStatus.FAILED
    assert final_iteration.status == IterationStatus.FAILED
    assert [w.id for w in closed] == [workflow.id]


def test_smoke_success_continue_then_terminal(
    workflow_store, iteration_store, attempt_store, task_store, task_center_run_id
):
    workflow_lifecycle, iteration_coordinators, closed = _build(
        workflow_store, iteration_store, attempt_store, task_store
    )
    workflow = _root_workflow(
        workflow_lifecycle, task_store, task_center_run_id, goal="initial-goal"
    )
    iteration1, _ = workflow_lifecycle.create_iteration_with_coordinator(
        workflow_id=workflow.id
    )
    _drive_iteration(
        iteration_coordinators=iteration_coordinators,
        iteration_id=iteration1.id,
        attempt_store=attempt_store,
        verdict=(AttemptStatus.PASSED, None, "next-goal"),
    )
    refreshed = workflow_store.get(workflow.id)
    assert refreshed is not None
    assert len(refreshed.iteration_ids) == 2
    assert refreshed.is_open
    iteration2_id = refreshed.iteration_ids[1]
    iteration2 = iteration_store.get(iteration2_id)
    assert iteration2 is not None
    assert iteration2.iteration_goal == "next-goal"
    # Drive iteration 2 to terminal success.
    _drive_iteration(
        iteration_coordinators=iteration_coordinators,
        iteration_id=iteration2_id,
        attempt_store=attempt_store,
        verdict=(AttemptStatus.PASSED, None, None),
    )
    final_workflow = workflow_store.get(workflow.id)
    assert final_workflow is not None
    assert final_workflow.status == WorkflowStatus.SUCCEEDED
    assert [w.id for w in closed] == [workflow.id]
