"""Attempt stage advancement for AttemptOrchestrator.

Owns the launch/quiescence state machine for one attempt's generators and
evaluator. Calls back into the orchestrator's ``_close_attempt`` for the actual
attempt-closing transition; the orchestrator remains the only owner of
close-attempt state and the on_attempt_closed signal to ``IterationAttemptCoordinator``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from task_center._core.audit import TaskCenterAuditEmitter
from task_center._core.primitives import TaskCenterInvariantViolation
from task_center.attempt.state import (
    Attempt,
    AttemptFailReason,
    AttemptStage,
    AttemptStatus,
)
from task_center.attempt.deps import (
    AgentLaunch,
    AttemptDeps,
)
from task_center.attempt.launch import AgentLaunchFactory
from task_center.attempt.generator_dag import (
    ready_pending_generator_ids,
    summarize_generator_dag,
)
from task_center._core.task_state import (
    TaskCenterTaskRole,
    TaskCenterTaskStatus,
)

logger = logging.getLogger(__name__)


CloseAttemptCallback = Callable[[AttemptStatus, AttemptFailReason | None], None]


class AttemptStageAdvancer:
    """Advances generator/evaluator stages until the attempt blocks or closes."""

    def __init__(
        self,
        *,
        attempt_id: str,
        runtime: AttemptDeps,
        close_attempt: CloseAttemptCallback,
    ) -> None:
        self._attempt_id = attempt_id
        self._runtime = runtime
        self._close_attempt = close_attempt
        self._audit = TaskCenterAuditEmitter(runtime.audit_sink)

    # ---- public API -----------------------------------------------------

    def advance_ready_tasks(self) -> None:
        attempt = self._fresh_attempt()
        if attempt.is_closed:
            return
        # PLAN and CLOSED stages are no-ops.
        if attempt.stage == AttemptStage.GENERATE:
            self._advance_generator_stage(attempt)
        elif attempt.stage == AttemptStage.EVALUATE:
            self._advance_evaluator_stage(attempt)

    def _advance_generator_stage(self, attempt: Attempt) -> None:
        runtime = self._runtime
        task_records = runtime.task_store.list_generator_tasks_for_attempt(attempt.id)
        ready_ids = ready_pending_generator_ids(task_records)
        if ready_ids:
            launch_failed = False
            for task_id in ready_ids:
                if not self._launch_ready_generator(
                    attempt=attempt,
                    task_id=task_id,
                ):
                    launch_failed = True
            if launch_failed:
                self.advance_ready_tasks()
            return

        state = summarize_generator_dag(task_records)
        if not state.all_quiescent:
            return

        if state.any_failed_or_blocked:
            self._close_attempt(
                AttemptStatus.FAILED,
                AttemptFailReason.GENERATOR_FAILED,
            )
            return

        if state.all_done:
            self._start_evaluator_stage(attempt)

    def _advance_evaluator_stage(self, attempt: Attempt) -> None:
        if attempt.evaluator_task_id is None:
            raise TaskCenterInvariantViolation(
                f"Attempt {attempt.id!r} is evaluating with no evaluator task"
            )
        runtime = self._runtime
        evaluator_task = runtime.task_store.get_task(attempt.evaluator_task_id)
        if evaluator_task is None:
            raise TaskCenterInvariantViolation(
                f"Evaluator task {attempt.evaluator_task_id!r} not found"
            )
        status = TaskCenterTaskStatus(evaluator_task["status"])
        if status == TaskCenterTaskStatus.DONE:
            self._close_attempt(AttemptStatus.PASSED, None)
        elif status == TaskCenterTaskStatus.FAILED:
            self._close_attempt(
                AttemptStatus.FAILED,
                AttemptFailReason.EVALUATOR_FAILED,
            )

    def _mark_launch_failed(self, *, task_id: str, attempt_id: str, role: str) -> None:
        """Mark a task FAILED (if still RUNNING) and emit task_failed audit."""
        summary = f"{role} agent launch failed."
        runtime = self._runtime
        runtime.task_store.set_task_status_if_current(
            task_id,
            expected_status=TaskCenterTaskStatus.RUNNING.value,
            status=TaskCenterTaskStatus.FAILED.value,
            summary={"fail_reason": "agent_launch_failed", "summary": summary},
        )
        failed_task = runtime.task_store.get_task(task_id)
        if failed_task is not None:
            self._audit.task_failed(
                failed_task,
                attempt_id=attempt_id,
                fail_reason="agent_launch_failed",
                summary=summary,
            )

    def _launch_ready_generator(self, *, attempt: Attempt, task_id: str) -> bool:
        runtime = self._runtime
        current = runtime.task_store.get_task(task_id)
        if current is None:
            raise TaskCenterInvariantViolation(f"Generator task {task_id!r} not found")
        agent_name = str(current.get("agent_name") or "").strip()
        if not agent_name:
            raise TaskCenterInvariantViolation(
                f"Task {current.get('id')!r} has no persisted agent profile"
            )
        self._audit.task_ready(
            current,
            attempt_id=attempt.id,
            satisfied_dependency_ids=tuple(str(dep) for dep in current.get("needs", ()) or ()),
        )
        task = runtime.task_store.set_task_status(
            task_id, status=TaskCenterTaskStatus.RUNNING.value
        )
        self._audit.task_launched(task, attempt_id=attempt.id)
        try:
            launch = AgentLaunchFactory(runtime=runtime).for_generator(
                attempt=attempt,
                task=task,
                base_agent_name=agent_name,
            )
            if launch.context_packet_id is not None:
                runtime.task_store.set_task_context_packet_id(
                    task_id,
                    context_packet_id=launch.context_packet_id,
                )
            runtime.agent_launcher.launch(launch)
        except Exception:
            logger.exception(
                "AttemptStageAdvancer: generator launch failed",
                extra={"task_id": task_id, "attempt_id": attempt.id},
            )
            self._mark_launch_failed(task_id=task_id, attempt_id=attempt.id, role="Generator")
            return False
        return True

    def _launch_evaluator(self, launch: AgentLaunch) -> None:
        attempt_id = launch.attempt_id
        if attempt_id is None:
            raise TaskCenterInvariantViolation(
                f"Evaluator launch {launch.task_id!r} is missing attempt_id"
            )
        try:
            self._runtime.agent_launcher.launch(launch)
        except Exception:
            logger.exception(
                "AttemptStageAdvancer: evaluator launch failed",
                extra={
                    "task_id": launch.task_id,
                    "attempt_id": attempt_id,
                },
            )
            self._mark_launch_failed(
                task_id=launch.task_id,
                attempt_id=attempt_id,
                role="Evaluator",
            )
            self._close_attempt(AttemptStatus.FAILED, AttemptFailReason.EVALUATOR_FAILED)

    def _start_evaluator_stage(self, attempt: Attempt) -> None:
        if attempt.evaluator_task_id is not None:
            return
        runtime = self._runtime
        task_id = f"{attempt.id}:evaluator"
        try:
            launch = AgentLaunchFactory(runtime=runtime).for_reducer(attempt=attempt, task_id=task_id)
            ready_task = {
                "id": task_id,
                "task_center_run_id": launch.task_center_run_id,
                "role": TaskCenterTaskRole.REDUCER.value,
                "agent_name": launch.agent_name,
                "status": TaskCenterTaskStatus.PENDING.value,
                "needs": list(attempt.generator_task_ids),
                "task_center_attempt_id": attempt.id,
                "context_packet_id": launch.context_packet_id,
            }
            self._audit.task_ready(
                ready_task,
                attempt_id=attempt.id,
                satisfied_dependency_ids=tuple(attempt.generator_task_ids),
            )
            runtime.task_store.upsert_task(
                task_id=task_id,
                task_center_run_id=launch.task_center_run_id,
                role=TaskCenterTaskRole.REDUCER.value,
                agent_name=launch.agent_name,
                context_message=launch.context,
                status=TaskCenterTaskStatus.RUNNING.value,
                summaries=[],
                needs=list(attempt.generator_task_ids),
                task_center_attempt_id=attempt.id,
                context_packet_id=launch.context_packet_id,
                spawn_reason="attempt_evaluator",
            )
            task = runtime.task_store.get_task(task_id)
            if task is not None:
                self._audit.task_launched(task, attempt_id=attempt.id)
            runtime.attempt_store.set_evaluator_task_id(attempt.id, task_id)
            runtime.attempt_store.set_stage(attempt.id, AttemptStage.EVALUATE)
            self._launch_evaluator(launch)
        except Exception:
            logger.exception(
                "AttemptStageAdvancer: evaluator spawn failed",
                extra={"task_id": task_id, "attempt_id": attempt.id},
            )
            try:
                runtime.task_store.set_task_status_if_current(
                    task_id,
                    expected_status=TaskCenterTaskStatus.RUNNING.value,
                    status=TaskCenterTaskStatus.FAILED.value,
                    summary={
                        "fail_reason": "agent_launch_failed",
                        "summary": "Evaluator agent startup failed.",
                    },
                )
            except LookupError:
                pass
            self._close_attempt(
                AttemptStatus.FAILED,
                AttemptFailReason.EVALUATOR_FAILED,
            )
            raise

    def _fresh_attempt(self) -> Attempt:
        attempt = self._runtime.attempt_store.get(self._attempt_id)
        if attempt is None:
            raise TaskCenterInvariantViolation(f"Attempt {self._attempt_id!r} not found")
        return attempt
