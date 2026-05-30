"""TaskCenter executor submission context resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from task_center._core.primitives import attempt_id_from_task_id
from tools._framework.core.context import ToolExecutionContextService
from tools.submission.context.attempt import (
    AttemptSubmissionContext,
    AttemptSubmissionContextError,
    _resolve_attempt_context,
    _resolve_runtime_task,
)

if TYPE_CHECKING:
    from task_center import AttemptDeps, StartedWorkflow


@dataclass(frozen=True, slots=True)
class ExecutorSubmissionContext:
    """Unified context for executor-shaped terminal submissions.

    Tools call :meth:`submit_generator_success`,
    :meth:`submit_generator_failure`, or :meth:`start_delegated_workflow`
    for attempt-bound generator tasks.
    """

    task_center_task_id: str
    task: dict[str, Any]
    runtime: AttemptDeps
    attempt_ctx: AttemptSubmissionContext

    @property
    def attempt_id(self) -> str:
        return self.attempt_ctx.attempt.id

    def submit_generator_success(
        self, *, outcome: str, artifacts: list[str]
    ) -> None:
        from task_center import GeneratorSubmission

        self.attempt_ctx.orchestrator.apply_generator_submission(
            GeneratorSubmission(
                attempt_id=self.attempt_ctx.attempt.id,
                task_id=self.task_center_task_id,
                status="success",
                outcome=outcome,
                terminal_tool_result={
                    "generator_role": "executor",
                    "artifacts": artifacts,
                },
            )
        )

    def submit_generator_failure(self, *, outcome: str) -> None:
        from task_center import GeneratorSubmission

        self.attempt_ctx.orchestrator.apply_generator_submission(
            GeneratorSubmission(
                attempt_id=self.attempt_ctx.attempt.id,
                task_id=self.task_center_task_id,
                status="failed",
                outcome=outcome,
                terminal_tool_result={
                    "generator_role": "executor",
                },
            )
        )

    def submit_executor_success(
        self, *, outcome: str, artifacts: list[str]
    ) -> None:
        self.submit_generator_success(outcome=outcome, artifacts=artifacts)

    def submit_executor_blocker(self, *, outcome: str) -> None:
        self.submit_generator_failure(outcome=outcome)

    def start_delegated_workflow(
        self, *, goal_handoff: str
    ) -> StartedWorkflow:
        from task_center import WorkflowStarter

        coordinator = WorkflowStarter(runtime=self.runtime)
        return coordinator.start(
            prompt=goal_handoff,
            parent_task_id=self.task_center_task_id,
        )


def resolve_executor_submission_context(
    context: ToolExecutionContextService,
) -> ExecutorSubmissionContext:
    """Resolve a unified executor submission context.

    Executor terminal tools are valid only for attempt-bound generator tasks.
    """
    runtime, task, task_id = _resolve_runtime_task(context)
    attempt_id = attempt_id_from_task_id(task_id) or ""
    if not attempt_id:
        raise AttemptSubmissionContextError(
            f"TaskCenter task {task_id!r} is not attempt-bound; executor "
            "terminal submissions require a generator task."
        )
    attempt_ctx = _resolve_attempt_context(
        runtime=runtime, task=task, task_id=task_id, context=context
    )
    return ExecutorSubmissionContext(
        task_center_task_id=task_id,
        task=task,
        runtime=runtime,
        attempt_ctx=attempt_ctx,
    )


__all__ = [
    "ExecutorSubmissionContext",
    "resolve_executor_submission_context",
]
