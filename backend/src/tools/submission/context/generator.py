"""TaskCenter generator submission context resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from workflow import TaskCenterInvariantViolation
from workflow._core.invariants import assert_generator_task_for_submission
from tools._framework.core.context import ToolExecutionContextService
from tools.submission.context.attempt import (
    AttemptSubmissionContext,
    AttemptSubmissionContextError,
    _resolve_attempt_context,
    _resolve_runtime_task,
)

if TYPE_CHECKING:
    from workflow import AttemptDeps, StartedWorkflow


@dataclass(frozen=True, slots=True)
class GeneratorSubmissionContext:
    """Unified context for generator-shaped terminal submissions.

    Tools call :meth:`submit_generator_outcome` or :meth:`start_delegated_workflow`
    for attempt-bound generator tasks.
    """

    task_center_task_id: str
    task: dict[str, Any]
    runtime: AttemptDeps
    attempt_ctx: AttemptSubmissionContext

    @property
    def attempt_id(self) -> str:
        return self.attempt_ctx.attempt.id

    def submit_generator_outcome(
        self, *, status: Literal["success", "failed"], outcome: str
    ) -> None:
        from workflow import GeneratorSubmission

        self.attempt_ctx.orchestrator.apply_generator_submission(
            GeneratorSubmission(
                attempt_id=self.attempt_ctx.attempt.id,
                task_id=self.task_center_task_id,
                status=status,
                outcome=outcome,
                terminal_tool_result={
                    "generator_role": "executor",
                },
            )
        )

    def start_delegated_workflow(
        self, *, goal_handoff: str
    ) -> StartedWorkflow:
        from workflow import WorkflowStarter

        coordinator = WorkflowStarter(runtime=self.runtime)
        return coordinator.start(
            prompt=goal_handoff,
            parent_task_id=self.task_center_task_id,
        )


def resolve_generator_submission_context(
    context: ToolExecutionContextService,
) -> GeneratorSubmissionContext:
    """Resolve a unified generator submission context.

    Generator terminal tools are valid only for attempt-bound generator tasks.
    """
    runtime, task, task_id = _resolve_runtime_task(context)
    attempt_id = str(task.get("attempt_id") or "")
    if not attempt_id:
        raise AttemptSubmissionContextError(
            f"TaskCenter task {task_id!r} is not attempt-bound; generator "
            "terminal submissions require a generator task."
        )
    attempt_ctx = _resolve_attempt_context(
        runtime=runtime, task=task, task_id=task_id, context=context
    )
    try:
        assert_generator_task_for_submission(task, attempt_ctx.attempt)
    except TaskCenterInvariantViolation as exc:
        raise AttemptSubmissionContextError(str(exc)) from exc
    return GeneratorSubmissionContext(
        task_center_task_id=task_id,
        task=task,
        runtime=runtime,
        attempt_ctx=attempt_ctx,
    )


__all__ = [
    "GeneratorSubmissionContext",
    "resolve_generator_submission_context",
]
