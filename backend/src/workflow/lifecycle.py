"""Workflow lifecycle coordination.

``WorkflowLifecycle`` creates delegated workflows, extends iteration chains, and
persists the final workflow projection on close. Workflows are launched from a
Task and inspected through workflow tools; closing a workflow no longer mutates
the launching Task directly.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from workflow._core.invariants import (
    assert_predecessor_has_deferred_goal_for_next_iteration,
    assert_workflow_open,
    assert_iteration_id_unique_in_workflow,
    assert_iteration_sequence_contiguous,
)
from workflow._core.outcomes import records_json, workflow_outcomes
from workflow._core.persistence import (
    AttemptStoreProtocol,
    WorkflowStoreProtocol,
    IterationStoreProtocol,
    TaskStoreProtocol,
)
from workflow._core.primitives import WorkflowInvariantViolation, WorkflowLifecycleConfig
from workflow._core.state import (
    Iteration,
    IterationCreationReason,
    IterationStatus,
    Workflow,
    WorkflowStatus,
)
from workflow.attempt.orchestrator_registry import AttemptOrchestratorRegistry
from workflow.iteration import (
    IterationAttemptCoordinator,
    OpenIterationCoordinatorRegistry,
    OrchestratorFactory,
)

logger = logging.getLogger(__name__)


class WorkflowLifecycle:
    """Coordinates one workflow's iteration chain and close routing."""

    def __init__(
        self,
        *,
        workflow_store: WorkflowStoreProtocol,
        iteration_store: IterationStoreProtocol,
        attempt_store: AttemptStoreProtocol,
        iteration_coordinators: OpenIterationCoordinatorRegistry,
        config: WorkflowLifecycleConfig,
        orchestrator_registry: AttemptOrchestratorRegistry,
        orchestrator_factory: OrchestratorFactory | None = None,
        task_store: TaskStoreProtocol | None = None,
    ) -> None:
        self._workflow_store = workflow_store
        self._iteration_store = iteration_store
        self._attempt_store = attempt_store
        self._iteration_coordinators = iteration_coordinators
        self._config = config
        self._orchestrator_registry = orchestrator_registry
        self._orchestrator_factory = orchestrator_factory
        self._task_store = task_store

    def create_workflow(
        self,
        *,
        request_id: str,
        parent_task_id: str,
        workflow_goal: str,
    ) -> Workflow:
        return self._workflow_store.insert(
            request_id=request_id,
            parent_task_id=parent_task_id,
            workflow_goal=workflow_goal,
        )

    def create_iteration_with_coordinator(
        self, *, workflow_id: str
    ) -> tuple[Iteration, IterationAttemptCoordinator]:
        """Create the workflow's next iteration and register its coordinator.

        The first iteration (workflow has none yet) is sequence 1 carrying the
        workflow goal. A later iteration continues the predecessor's deferred
        goal; the predecessor must be SUCCEEDED with a recorded
        ``deferred_goal_for_next_iteration``.
        """
        workflow = self._require_workflow(workflow_id)
        assert_workflow_open(workflow)
        if not workflow.iteration_ids:
            sequence_no = 1
            creation_reason = IterationCreationReason.INITIAL
            iteration_goal = workflow.workflow_goal
        else:
            previous = self._iteration_store.get(workflow.iteration_ids[-1])
            if previous is None:
                raise WorkflowInvariantViolation(
                    f"Workflow {workflow_id!r} predecessor iteration "
                    f"{workflow.iteration_ids[-1]!r} not found"
                )
            assert_predecessor_has_deferred_goal_for_next_iteration(previous)
            deferred_goal = previous.deferred_goal_for_next_iteration
            if deferred_goal is None:  # pragma: no cover - guarded by invariant above
                raise WorkflowInvariantViolation(
                    f"Iteration {previous.id!r} has no deferred goal"
                )
            sequence_no = previous.sequence_no + 1
            creation_reason = IterationCreationReason.DEFERRED_GOAL_CONTINUATION
            iteration_goal = deferred_goal
        assert_iteration_sequence_contiguous(workflow, new_sequence_no=sequence_no)
        return self._insert_iteration_and_register_coordinator(
            workflow=workflow,
            sequence_no=sequence_no,
            creation_reason=creation_reason,
            iteration_goal=iteration_goal,
        )

    def handle_iteration_closed(
        self,
        *,
        iteration_id: str,
        succeeded: bool,
        deferred_goal: str | None,
    ) -> None:
        iteration = self._iteration_store.get(iteration_id)
        if iteration is None:
            raise WorkflowInvariantViolation(f"Iteration {iteration_id!r} not found")
        try:
            if succeeded and deferred_goal is not None:
                next_iteration, next_coordinator = self.create_iteration_with_coordinator(
                    workflow_id=iteration.workflow_id
                )
                self._start_deferred_iteration(
                    next_iteration=next_iteration,
                    next_coordinator=next_coordinator,
                )
            else:
                self.close_workflow(
                    workflow_id=iteration.workflow_id,
                    succeeded=succeeded,
                )
        finally:
            self._iteration_coordinators.deregister(iteration.id)

    def close_workflow(
        self,
        *,
        workflow_id: str,
        succeeded: bool,
    ) -> Workflow:
        workflow = self._require_workflow(workflow_id)
        assert_workflow_open(workflow)
        outcomes = records_json(
            workflow_outcomes(workflow, iteration_store=self._iteration_store)
        )
        updated = self._workflow_store.set_status(
            workflow_id,
            status=WorkflowStatus.SUCCEEDED if succeeded else WorkflowStatus.FAILED,
            closed_at=datetime.now(UTC),
            outcomes=outcomes,
        )
        return updated

    # ---- internals ------------------------------------------------------

    def _require_workflow(self, workflow_id: str) -> Workflow:
        workflow = self._workflow_store.get(workflow_id)
        if workflow is None:
            raise WorkflowInvariantViolation(f"Workflow {workflow_id!r} not found")
        return workflow

    def _append_iteration_id(self, workflow: Workflow, iteration_id: str) -> Workflow:
        assert_iteration_id_unique_in_workflow(workflow, iteration_id)
        return self._workflow_store.append_iteration_id(workflow.id, iteration_id)

    def _insert_iteration_and_register_coordinator(
        self,
        *,
        workflow: Workflow,
        sequence_no: int,
        creation_reason: IterationCreationReason,
        iteration_goal: str,
    ) -> tuple[Iteration, IterationAttemptCoordinator]:
        iteration = self._iteration_store.insert(
            workflow_id=workflow.id,
            sequence_no=sequence_no,
            creation_reason=creation_reason,
            iteration_goal=iteration_goal,
            attempt_budget=self._config.default_attempt_budget,
        )
        self._append_iteration_id(workflow, iteration.id)
        coordinator = IterationAttemptCoordinator(
            iteration_id=iteration.id,
            iteration_store=self._iteration_store,
            attempt_store=self._attempt_store,
            on_iteration_closed=self.handle_iteration_closed,
            orchestrator_factory=self._orchestrator_factory,
            task_store=self._task_store,
        )
        self._iteration_coordinators.register(coordinator)
        return iteration, coordinator

    def _start_deferred_iteration(
        self,
        *,
        next_iteration: Iteration,
        next_coordinator: IterationAttemptCoordinator,
    ) -> None:
        if self._orchestrator_factory is None:
            return
        try:
            next_coordinator.create_and_start_first_attempt()
        except Exception:
            logger.exception(
                "WorkflowLifecycle: continuation attempt creation failed",
                extra={"iteration_id": next_iteration.id},
            )
            self._iteration_store.set_status(
                next_iteration.id,
                status=IterationStatus.CANCELLED,
                closed_at=datetime.now(UTC),
            )
            self._iteration_coordinators.deregister(next_iteration.id)
            self.close_workflow(
                workflow_id=next_iteration.workflow_id,
                succeeded=False,
            )


__all__ = [
    "WorkflowLifecycle",
]
