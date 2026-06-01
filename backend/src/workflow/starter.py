"""WorkflowStarter — single safe path from a running Task to Workflow execution.

Delegated workflows are non-terminal background work. The launching Task stays
``running``; lifecycle state lives on the workflow/iteration/attempt rows and
is inspected through workflow tools.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from workflow._core.primitives import TaskCenterInvariantViolation
from workflow._core.state import (
    AttemptFailReason,
    AttemptStatus,
    IterationStatus,
    Workflow,
    WorkflowStatus,
)
from task import TaskStatus
from workflow.attempt.launch import AttemptDeps
from workflow.attempt.orchestrator import AttemptOrchestrator
from workflow.iteration import OrchestratorFactory
from workflow.lifecycle import WorkflowLifecycle

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class StartedWorkflow:
    parent_task_id: str
    parent_attempt_id: str | None
    workflow_id: str
    iteration_id: str
    attempt_id: str


class WorkflowStarter:
    """Single orchestration entry point for task-launched workflow start."""

    def __init__(
        self,
        *,
        runtime: AttemptDeps,
        orchestrator_factory: OrchestratorFactory | None = None,
    ) -> None:
        self._runtime = runtime
        self._orchestrator_factory = orchestrator_factory or (
            lambda attempt, on_attempt_closed: AttemptOrchestrator(
                attempt=attempt,
                on_attempt_closed=on_attempt_closed,
                runtime=self._runtime,
            )
        )

    def start(self, *, prompt: str, parent_task_id: str) -> StartedWorkflow:
        prompt = prompt.strip()
        if not prompt:
            raise TaskCenterInvariantViolation("Workflow prompt must be nonblank.")
        parent_task = self._assert_parent_running_and_no_open_child(parent_task_id)
        request_id = str(parent_task.get("request_id") or "")
        if not request_id.strip():
            raise TaskCenterInvariantViolation(
                f"Parent task {parent_task_id!r} has no request id."
            )
        parent_attempt_id = str(parent_task.get("attempt_id") or "") or None

        lifecycle = self._build_workflow_lifecycle()
        workflow = lifecycle.create_workflow(
            request_id=request_id,
            parent_task_id=parent_task_id,
            workflow_goal=prompt,
        )
        iteration, iteration_coordinator = lifecycle.create_iteration_with_coordinator(
            workflow_id=workflow.id,
        )

        try:
            attempt = iteration_coordinator.create_and_start_first_attempt()
        except Exception:
            refreshed = self._runtime.iteration_store.get(iteration.id)
            attempt_id = refreshed.latest_attempt_id if refreshed else None
            self._compensate_failed_start(
                workflow=workflow,
                iteration_id=iteration.id,
                attempt_id=attempt_id,
            )
            raise

        return StartedWorkflow(
            parent_task_id=parent_task_id,
            parent_attempt_id=parent_attempt_id,
            workflow_id=workflow.id,
            iteration_id=iteration.id,
            attempt_id=attempt.id,
        )

    def _build_workflow_lifecycle(self) -> WorkflowLifecycle:
        iteration_coordinators = self._runtime.iteration_coordinators
        if iteration_coordinators is None:
            raise TaskCenterInvariantViolation("WorkflowStarter requires open iteration coordinators.")
        return WorkflowLifecycle(
            workflow_store=self._runtime.workflow_store,
            iteration_store=self._runtime.iteration_store,
            attempt_store=self._runtime.attempt_store,
            iteration_coordinators=iteration_coordinators,
            config=self._runtime.lifecycle_config,
            orchestrator_registry=self._runtime.orchestrator_registry,
            orchestrator_factory=self._orchestrator_factory,
            task_store=self._runtime.task_store,
        )

    def _assert_parent_running_and_no_open_child(self, parent_task_id: str) -> dict[str, Any]:
        task = self._runtime.task_store.get_task(parent_task_id)
        if task is None:
            raise TaskCenterInvariantViolation(f"TaskCenter task {parent_task_id!r} was not found.")
        if task.get("status") != TaskStatus.RUNNING.value:
            raise TaskCenterInvariantViolation(
                f"TaskCenter task {parent_task_id!r} is not running; "
                "delegated workflow start requires a running parent task."
            )
        open_workflows = [
            r for r in self._runtime.workflow_store.list_for_parent_task(parent_task_id) if r.is_open
        ]
        if open_workflows:
            raise TaskCenterInvariantViolation(
                f"TaskCenter task {parent_task_id!r} already has an open "
                f"delegated workflow {open_workflows[0].id!r}."
            )
        return task

    def _compensate_failed_start(
        self,
        *,
        workflow: Workflow,
        iteration_id: str,
        attempt_id: str | None,
    ) -> None:
        """Best-effort rollback: attempt -> iteration -> workflow."""
        now = datetime.now(UTC)
        runtime = self._runtime

        def _do(step_name: str, action) -> bool:
            try:
                action()
                return True
            except Exception:
                logger.exception("WorkflowStart compensation step %r failed", step_name)
                return False

        _do("close_unstarted_attempt", lambda: self._close_unstarted_attempt(attempt_id, now=now))
        _do(
            "cancel_iteration",
            lambda: runtime.iteration_store.set_status(
                iteration_id, status=IterationStatus.CANCELLED, closed_at=now
            ),
        )
        _do(
            "cancel_workflow",
            lambda: runtime.workflow_store.set_status(
                workflow.id, status=WorkflowStatus.CANCELLED, closed_at=now
            ),
        )
        if runtime.iteration_coordinators is not None:
            runtime.iteration_coordinators.deregister(iteration_id)

    def _close_unstarted_attempt(self, attempt_id: str | None, *, now: datetime) -> None:
        if attempt_id is None:
            return
        attempt = self._runtime.attempt_store.get(attempt_id)
        if attempt is None or attempt.is_closed:
            return
        self._runtime.attempt_store.close(
            attempt_id,
            status=AttemptStatus.FAILED,
            fail_reason=AttemptFailReason.STARTUP_FAILED,
            closed_at=now,
        )
