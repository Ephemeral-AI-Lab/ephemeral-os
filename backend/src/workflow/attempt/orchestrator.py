"""AttemptOrchestrator state machine."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from workflow._core.invariants import (
    assert_attempt_not_closed,
    assert_attempt_stage,
    assert_generator_task_for_submission,
    assert_reducer_task_for_submission,
    assert_task_belongs_to_attempt,
    assert_valid_attempt_close,
)
from workflow._core.outcomes import (
    ExecutionRole,
    execution_outcome_for_submission,
    project_attempt_outcomes,
    to_record,
)
from workflow._core.primitives import (
    WorkflowInvariantViolation,
    planner_task_id,
)
from workflow._core.state import (
    Attempt,
    AttemptFailReason,
    AttemptStage,
    AttemptStatus,
)
from task import (
    AgentRole,
    TaskStatus,
)
from workflow.attempt.launch import AgentLaunchFactory, AttemptDeps
from workflow.attempt.run_stage import AttemptStageAdvancer
from workflow.submissions import (
    GeneratorSubmission,
    PlannerFailureSubmission,
    PlannerSubmission,
    ReducerSubmission,
)

logger = logging.getLogger(__name__)


class AttemptOrchestrator:
    """Runs one planner -> plan-DAG (generators + reducers) harness attempt."""

    def __init__(
        self,
        *,
        attempt: Attempt,
        on_attempt_closed: Callable[[str], None],
        runtime: AttemptDeps,
    ) -> None:
        self._attempt = attempt
        self._on_attempt_closed = on_attempt_closed
        self._runtime = runtime

        self._stage_advancer = AttemptStageAdvancer(
            attempt_id=attempt.id,
            runtime=runtime,
            close_attempt=self._close_attempt,
        )

    @property
    def attempt_id(self) -> str:
        return self._attempt.id

    def start(self) -> None:
        runtime = self._runtime
        attempt = self._assert_stage(AttemptStage.PLAN)
        if attempt.status != AttemptStatus.RUNNING:
            raise WorkflowInvariantViolation(f"Attempt {attempt.id!r} is not running")
        if attempt.planner_task_id is not None:
            raise WorkflowInvariantViolation(f"Attempt {attempt.id!r} already has a planner task")

        task_id = planner_task_id(attempt.id)
        runtime.orchestrator_registry.register(self)
        try:
            launch = AgentLaunchFactory(runtime=runtime).for_planner(attempt=attempt, task_id=task_id)
            runtime.task_store.upsert_task(
                task_id=task_id,
                request_id=launch.request_id,
                role=AgentRole.PLANNER.value,
                agent_name=launch.agent_name,
                instruction=launch.context,
                status=TaskStatus.RUNNING.value,
                outcomes=[],
                needs=[],
                workflow_id=launch.workflow_id,
                iteration_id=attempt.iteration_id,
                attempt_id=attempt.id,
            )
            runtime.attempt_store.set_planner_task_id(attempt.id, task_id)
            runtime.agent_launcher.launch(launch)
            self._stage_advancer.advance_ready_tasks()
        except Exception:
            self._mark_startup_failed(planner_task_id=task_id)
            raise

    def apply_plan_submission(self, submission: PlannerSubmission) -> None:
        self._assert_submission_attempt(submission.attempt_id)
        if (
            submission.kind == "completes"
            and submission.deferred_goal_for_next_iteration is not None
        ):
            raise WorkflowInvariantViolation(
                "Full plans cannot set deferred_goal_for_next_iteration"
            )
        if submission.kind == "defers" and submission.deferred_goal_for_next_iteration is None:
            raise WorkflowInvariantViolation(
                "Partial plans require deferred_goal_for_next_iteration"
            )

        attempt = self._validate_planner_submission(submission.planner_task_id)
        runtime = self._runtime
        runtime.task_store.set_task_status(
            submission.planner_task_id,
            status=TaskStatus.DONE.value,
            outcomes=[],
            terminal_tool_result={"kind": submission.kind},
        )
        runtime.attempt_store.set_deferred_goal(
            attempt.id,
            deferred_goal_for_next_iteration=submission.deferred_goal_for_next_iteration,
        )
        runtime.attempt_store.set_generator_task_ids(
            attempt.id, list(submission.generator_task_ids)
        )
        runtime.attempt_store.set_reducer_task_ids(
            attempt.id, list(submission.reducer_task_ids)
        )
        runtime.attempt_store.set_stage(attempt.id, AttemptStage.RUN)
        self._stage_advancer.advance_ready_tasks()

    def apply_planner_failure(self, submission: PlannerFailureSubmission) -> None:
        self._assert_submission_attempt(submission.attempt_id)
        self._validate_planner_submission(submission.planner_task_id)
        self._runtime.task_store.set_task_status(
            submission.planner_task_id,
            status=TaskStatus.FAILED.value,
            outcomes=[],
            terminal_tool_result={"fail_reason": submission.fail_reason},
        )
        self._close_attempt(AttemptStatus.FAILED, AttemptFailReason.TASK_FAILED)

    def apply_generator_submission(self, submission: GeneratorSubmission) -> None:
        self._assert_submission_attempt(submission.attempt_id)
        self._mark_generator(submission)
        self._stage_advancer.advance_ready_tasks()

    def apply_reducer_submission(self, submission: ReducerSubmission) -> None:
        self._assert_submission_attempt(submission.attempt_id)
        self._mark_reducer(submission)
        self._stage_advancer.advance_ready_tasks()

    # ---- internals ------------------------------------------------------

    def _validate_planner_submission(self, planner_task_id: str) -> Attempt:
        attempt = self._assert_stage(AttemptStage.PLAN)
        if attempt.planner_task_id != planner_task_id:
            raise WorkflowInvariantViolation(
                f"Planner submission task {planner_task_id!r} does not "
                f"match attempt planner {attempt.planner_task_id!r}"
            )
        planner_task = self._runtime.task_store.get_task(planner_task_id)
        if planner_task is None:
            raise WorkflowInvariantViolation(f"Planner task {planner_task_id!r} not found")
        assert_task_belongs_to_attempt(planner_task, attempt)
        if planner_task["role"] != AgentRole.PLANNER.value:
            raise WorkflowInvariantViolation(f"Task {planner_task_id!r} is not a planner task")
        return attempt

    def _mark_generator(self, submission: GeneratorSubmission) -> None:
        attempt = self._assert_stage(AttemptStage.RUN)
        task = self._runtime.task_store.get_task(submission.task_id)
        if task is None:
            raise WorkflowInvariantViolation(f"Generator task {submission.task_id!r} not found")
        assert_generator_task_for_submission(task, attempt)
        self._write_submission_status(
            task=task,
            task_id=submission.task_id,
            role="generator",
            status=submission.status,
            outcome=submission.outcome,
            terminal_tool_result=submission.terminal_tool_result,
        )

    def _mark_reducer(self, submission: ReducerSubmission) -> None:
        attempt = self._assert_stage(AttemptStage.RUN)
        if submission.task_id not in attempt.reducer_task_ids:
            raise WorkflowInvariantViolation(
                f"Reducer submission task {submission.task_id!r} is not a "
                f"reducer of attempt {attempt.id!r}"
            )
        task = self._runtime.task_store.get_task(submission.task_id)
        if task is None:
            raise WorkflowInvariantViolation(f"Reducer task {submission.task_id!r} not found")
        assert_reducer_task_for_submission(task, attempt)
        self._write_submission_status(
            task=task,
            task_id=submission.task_id,
            role="reducer",
            status=submission.status,
            outcome=submission.outcome,
            terminal_tool_result=submission.terminal_tool_result,
        )

    def _write_submission_status(
        self,
        *,
        task: dict[str, Any],
        task_id: str,
        role: ExecutionRole,
        status: str,
        outcome: str,
        terminal_tool_result: dict[str, Any],
    ) -> None:
        if task["status"] != TaskStatus.RUNNING.value:
            raise WorkflowInvariantViolation(f"{role.capitalize()} task {task_id!r} is not running")
        if status == "success":
            task_status = TaskStatus.DONE
        elif status == "failed":
            task_status = TaskStatus.FAILED
        else:
            task_status = TaskStatus.FAILED
        execution_status = "success" if task_status == TaskStatus.DONE else "failed"
        result = execution_outcome_for_submission(
            task_id=task_id,
            role=role,
            status=execution_status,
            outcome=outcome,
        )
        self._runtime.task_store.set_task_status(
            task_id,
            status=task_status.value,
            outcomes=[to_record(result)],
            terminal_tool_result=terminal_tool_result,
        )

    def _close_attempt(
        self,
        status: AttemptStatus,
        fail_reason: AttemptFailReason | None,
    ) -> None:
        assert_valid_attempt_close(status=status, fail_reason=fail_reason)
        attempt = self._fresh_attempt()
        assert_attempt_not_closed(attempt)
        if attempt.status != AttemptStatus.RUNNING:
            raise WorkflowInvariantViolation(f"Attempt {attempt.id!r} is not running")
        self._runtime.attempt_store.close(
            attempt.id,
            status=status,
            fail_reason=fail_reason,
            outcomes=[
                to_record(outcome)
                for outcome in project_attempt_outcomes(attempt, self._runtime.task_store)
            ],
            closed_at=datetime.now(UTC),
        )
        self._runtime.orchestrator_registry.deregister(attempt.id)
        self._on_attempt_closed(attempt.id)

    def _mark_startup_failed(self, *, planner_task_id: str) -> None:
        # Owns planner-task cleanup + registry deregistration. IterationAttemptCoordinator's
        # _close_attempt_after_startup_failure (its catch in
        # _start_orchestrator_if_configured) owns the attempt-close in both
        # paths — factory raises and start() raises.
        runtime = self._runtime
        runtime.orchestrator_registry.deregister(self._attempt.id)
        try:
            runtime.task_store.set_task_status_if_current(
                planner_task_id,
                expected_status=TaskStatus.RUNNING.value,
                status=TaskStatus.FAILED.value,
                outcomes=[],
                terminal_tool_result={"fail_reason": AttemptFailReason.STARTUP_FAILED.value},
            )
        except LookupError:
            pass
        except Exception:
            logger.exception(
                "AttemptOrchestrator: startup task cleanup failed",
            )

    def _fresh_attempt(self) -> Attempt:
        attempt = self._runtime.attempt_store.get(self._attempt.id)
        if attempt is None:
            raise WorkflowInvariantViolation(f"Attempt {self._attempt.id!r} not found")
        self._attempt = attempt
        return attempt

    def _assert_stage(self, expected: AttemptStage) -> Attempt:
        attempt = self._fresh_attempt()
        assert_attempt_not_closed(attempt)
        assert_attempt_stage(attempt, expected)
        return attempt

    def _assert_submission_attempt(self, attempt_id: str) -> None:
        if attempt_id != self._attempt.id:
            raise WorkflowInvariantViolation(
                f"Submission attempt {attempt_id!r} does not match orchestrator "
                f"attempt {self._attempt.id!r}"
            )
