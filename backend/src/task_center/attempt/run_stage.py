"""Attempt RUN-stage advancement for AttemptOrchestrator.

Owns the launch/quiescence state machine for one attempt's plan tasks
(generators + reducers, scheduled as one DAG). Calls back into the
orchestrator's ``_close_attempt`` for the actual attempt-closing transition; the
orchestrator remains the only owner of close-attempt state and the
on_attempt_closed signal to ``IterationAttemptCoordinator``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from task_center._core.audit import TaskCenterAuditEmitter
from task_center._core.outcomes import execution_outcome_for_submission, to_record
from task_center._core.primitives import TaskCenterInvariantViolation
from task_center._core.state import (
    Attempt,
    AttemptFailReason,
    AttemptStage,
    AttemptStatus,
)
from task_center._core.task_state import (
    TaskCenterTaskRole,
    TaskCenterTaskStatus,
)
from task_center.attempt.launch import AgentLaunchFactory, AttemptDeps
from task_center.attempt.plan_dag import dag_status, ready_pending_plan_ids

logger = logging.getLogger(__name__)


CloseAttemptCallback = Callable[[AttemptStatus, AttemptFailReason | None], None]


class AttemptStageAdvancer:
    """Advances the RUN stage until the attempt blocks or closes."""

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
        # PLAN and CLOSED stages are no-ops; the single RUN stage schedules the
        # plan's generator + reducer tasks to quiescence.
        if attempt.stage == AttemptStage.RUN:
            self._advance_run_stage(attempt)

    def _advance_run_stage(self, attempt: Attempt) -> None:
        records = self._plan_task_records(attempt)
        ready_ids = ready_pending_plan_ids(records)
        if ready_ids:
            roles = {r["task_id"]: r.get("role") for r in records}
            launch_failed = False
            for task_id in ready_ids:
                if not self._launch_ready_plan_task(
                    attempt=attempt,
                    task_id=task_id,
                    role=roles.get(task_id),
                ):
                    launch_failed = True
            if launch_failed:
                self.advance_ready_tasks()
            return

        state = dag_status(records)
        if not state.all_quiescent:
            return

        if state.any_failed_or_blocked:
            self._close_attempt(AttemptStatus.FAILED, AttemptFailReason.TASK_FAILED)
            return

        if state.all_done:
            self._close_attempt(AttemptStatus.PASSED, None)

    def _plan_task_records(self, attempt: Attempt) -> list[dict]:
        """All plan-task rows (generators + reducers) sourced by id from the attempt."""
        runtime = self._runtime
        records: list[dict] = []
        for task_id in (*attempt.generator_task_ids, *attempt.reducer_task_ids):
            task = runtime.task_store.get_task(task_id)
            if task is None:
                raise TaskCenterInvariantViolation(f"Plan task {task_id!r} not found")
            records.append(task)
        return records

    def _mark_launch_failed(self, *, task_id: str, attempt_id: str, role: str) -> None:
        """Mark a task FAILED (if still RUNNING) and emit task_failed audit."""
        summary = f"{role} agent launch failed."
        runtime = self._runtime
        runtime.task_store.set_task_status_if_current(
            task_id,
            expected_status=TaskCenterTaskStatus.RUNNING.value,
            status=TaskCenterTaskStatus.FAILED.value,
            outcomes=[
                to_record(
                    execution_outcome_for_submission(
                        task_id=task_id,
                        role="reducer" if role == "Reducer" else "generator",
                        status="failed",
                        outcome=summary,
                    )
                )
            ],
            terminal_tool_result={"fail_reason": "agent_launch_failed"},
        )
        failed_task = runtime.task_store.get_task(task_id)
        if failed_task is not None:
            self._audit.task_failed(
                failed_task,
                attempt_id=attempt_id,
                fail_reason="agent_launch_failed",
                summary=summary,
            )

    def _launch_ready_plan_task(
        self, *, attempt: Attempt, task_id: str, role: str | None
    ) -> bool:
        runtime = self._runtime
        current = runtime.task_store.get_task(task_id)
        if current is None:
            raise TaskCenterInvariantViolation(f"Plan task {task_id!r} not found")
        is_reducer = role == TaskCenterTaskRole.REDUCER.value
        role_label = "Reducer" if is_reducer else "Generator"
        agent_name = str(current.get("agent_name") or "").strip()
        if not agent_name:
            raise TaskCenterInvariantViolation(
                f"Task {current.get('task_id')!r} has no persisted agent profile"
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
            factory = AgentLaunchFactory(runtime=runtime)
            if is_reducer:
                launch = factory.for_reducer(attempt=attempt, task=task)
            else:
                launch = factory.for_generator(
                    attempt=attempt, task=task, base_agent_name=agent_name
                )
            runtime.agent_launcher.launch(launch)
        except Exception:
            logger.exception(
                "AttemptStageAdvancer: plan task launch failed",
                extra={"task_id": task_id, "attempt_id": attempt.id},
            )
            self._mark_launch_failed(task_id=task_id, attempt_id=attempt.id, role=role_label)
            return False
        return True

    def _fresh_attempt(self) -> Attempt:
        attempt = self._runtime.attempt_store.get(self._attempt_id)
        if attempt is None:
            raise TaskCenterInvariantViolation(f"Attempt {self._attempt_id!r} not found")
        return attempt
