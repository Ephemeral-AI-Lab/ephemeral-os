"""TaskCenter attempt submission context resolution.

The submission tools (``submit_execution_success``, ``submit_execution_failure``,
``request_mission_solution``) live in two modes:

1. **Attempt mode** — the task is attached to a :class:`Attempt` and a
   running :class:`AttemptOrchestrator`. Terminal events flow through
   the orchestrator's ``apply_*`` methods.
2. **Entry mode** — the task is the attempt-less top-level entry executor,
   identified by ``task_center_attempt_id is None`` on the task row.
   Terminal events flow through :class:`EntryTaskController`.

:func:`resolve_attempt_submission_context` keeps the attempt-only resolver
for callers that strictly require an attempt (gates, evaluator surfaces). The
executor resolver
:func:`resolve_executor_submission_context` returns
:class:`ExecutorSubmissionContext` — a tagged shape exposing attempt-shape-agnostic
operations (``submit_executor_success`` / ``submit_executor_failure`` /
``start_mission_request``) that internally branch on which mode applies.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from task_center.api import (
    Attempt,
    AttemptOrchestrator,
    AttemptRuntime,
    EntryTaskController,
    Episode,
    GeneratorSubmission,
    Mission,
    MissionStarter,
    StartedMission,
    TaskCenterInvariantViolation,
)
from tools.core.context import ToolExecutionContextService


class AttemptSubmissionContextError(RuntimeError):
    """User-facing submission context resolution failure."""


@dataclass(frozen=True, slots=True)
class AttemptSubmissionContext:
    """Attempt-bound submission context.

    Resolved when the executor task is attached to an Attempt. Tools
    that strictly require attempt context (e.g. ``submit_evaluation``) keep
    using this resolver.
    """

    task_center_task_id: str
    task: dict[str, Any]
    attempt: Attempt
    episode: Episode
    mission: Mission
    runtime: AttemptRuntime
    orchestrator: AttemptOrchestrator


@dataclass(frozen=True, slots=True)
class ExecutorSubmissionContext:
    """Unified context for executor-shaped terminal submissions.

    Tools call :meth:`submit_executor_success`,
    :meth:`submit_executor_failure`, or :meth:`start_mission_request`
    without knowing whether the task is attempt-bound or entry-mode. The
    context dispatches to the right backend (orchestrator vs entry
    controller) internally.

    Exactly one of ``attempt_ctx`` and ``entry_controller`` is set.
    """

    task_center_task_id: str
    task: dict[str, Any]
    runtime: AttemptRuntime
    attempt_ctx: AttemptSubmissionContext | None
    entry_controller: EntryTaskController | None

    @property
    def is_entry_mode(self) -> bool:
        return self.entry_controller is not None

    @property
    def attempt_id(self) -> str | None:
        return self.attempt_ctx.attempt.id if self.attempt_ctx is not None else None

    # ---- operations -------------------------------------------------------

    def submit_executor_success(
        self, *, summary: str, artifacts: list[str]
    ) -> None:
        if self.attempt_ctx is not None:
            self.attempt_ctx.orchestrator.apply_generator_submission(
                GeneratorSubmission(
                    attempt_id=self.attempt_ctx.attempt.id,
                    task_id=self.task_center_task_id,
                    outcome="success",
                    summary=summary,
                    payload={
                        "generator_role": "executor",
                        "artifacts": artifacts,
                    },
                )
            )
            return
        assert self.entry_controller is not None
        self.entry_controller.apply_executor_success(
            summary=summary, artifacts=artifacts
        )

    def submit_executor_failure(
        self, *, summary: str, reason: str, details: list[str]
    ) -> None:
        if self.attempt_ctx is not None:
            self.attempt_ctx.orchestrator.apply_generator_submission(
                GeneratorSubmission(
                    attempt_id=self.attempt_ctx.attempt.id,
                    task_id=self.task_center_task_id,
                    outcome="failure",
                    summary=summary,
                    payload={
                        "generator_role": "executor",
                        "reason": reason,
                        "details": details,
                    },
                )
            )
            return
        assert self.entry_controller is not None
        self.entry_controller.apply_executor_failure(
            summary=summary, reason=reason, details=details
        )

    def start_mission_request(
        self, *, goal: str
    ) -> StartedMission:
        coordinator = MissionStarter(runtime=self.runtime)
        return coordinator.start(
            parent_task_id=self.task_center_task_id,
            goal=goal,
        )


def resolve_attempt_submission_context(
    context: ToolExecutionContextService,
) -> AttemptSubmissionContext:
    """Resolve the current TaskCenter task into durable harness attempt context.

    Strict attempt mode — raises :class:`AttemptSubmissionContextError` if the
    task is not attached to an Attempt. Use this resolver from tools
    that genuinely require an attempt (planner submissions, evaluator
    submissions).
    """
    runtime, task, task_id = _resolve_runtime_task(context)
    return _resolve_attempt_context(
        runtime=runtime, task=task, task_id=task_id, context=context
    )


def resolve_executor_submission_context(
    context: ToolExecutionContextService,
) -> ExecutorSubmissionContext:
    """Resolve a unified executor submission context.

    Branches on whether the task row's ``task_center_attempt_id`` is
    set (attempt mode) or ``None`` (entry mode). Tools that accept either
    shape — ``submit_execution_success`` / ``submit_execution_failure`` /
    ``request_mission_solution`` — call this resolver and use the
    resulting :class:`ExecutorSubmissionContext` operations.
    """
    runtime, task, task_id = _resolve_runtime_task(context)
    attempt_id = str(task.get("task_center_attempt_id") or "")
    if attempt_id and not attempt_id.isspace():
        attempt_ctx = _resolve_attempt_context(
            runtime=runtime, task=task, task_id=task_id, context=context
        )
        return ExecutorSubmissionContext(
            task_center_task_id=task_id,
            task=task,
            runtime=runtime,
            attempt_ctx=attempt_ctx,
            entry_controller=None,
        )

    controller = runtime.entry_task_controller_for(task_id)
    if controller is None:
        raise AttemptSubmissionContextError(
            f"TaskCenter task {task_id!r} is attempt-less but no entry "
            "controller is bound to it; the spawn was set up incorrectly."
        )
    return ExecutorSubmissionContext(
        task_center_task_id=task_id,
        task=task,
        runtime=runtime,
        attempt_ctx=None,
        entry_controller=controller,
    )


def _resolve_runtime_task(
    context: ToolExecutionContextService,
) -> tuple[AttemptRuntime, dict[str, Any], str]:
    """Shared prelude: pull runtime + task row + task id from tool context."""
    runtime = context.get("attempt_runtime")
    if not isinstance(runtime, AttemptRuntime):
        raise AttemptSubmissionContextError(
            "Missing harness attempt runtime for this TaskCenter submission."
        )

    task_id = str(context.get("task_center_task_id") or "")
    if not task_id or task_id.isspace():
        raise AttemptSubmissionContextError(
            "Missing TaskCenter task id for this submission."
        )

    task = runtime.task_store.get_task(task_id)
    if task is None:
        raise AttemptSubmissionContextError(
            f"TaskCenter task {task_id!r} was not found."
        )
    return runtime, task, task_id


def _resolve_attempt_context(
    *,
    runtime: AttemptRuntime,
    task: dict[str, Any],
    task_id: str,
    context: ToolExecutionContextService,
) -> AttemptSubmissionContext:
    """Build :class:`AttemptSubmissionContext` from an already-fetched task.

    Shared between :func:`resolve_attempt_submission_context` and the
    attempt-mode branch of :func:`resolve_executor_submission_context` so the
    task row is fetched exactly once per call.
    """
    attempt_id = str(task.get("task_center_attempt_id") or "")
    if not attempt_id or attempt_id.isspace():
        raise AttemptSubmissionContextError(
            f"TaskCenter task {task_id!r} is not attached to a harness attempt."
        )

    metadata_attempt_id = str(context.get("task_center_attempt_id") or "")
    if metadata_attempt_id.isspace():
        raise AttemptSubmissionContextError(
            "TaskCenter attempt metadata is blank."
        )
    if metadata_attempt_id and metadata_attempt_id != attempt_id:
        raise AttemptSubmissionContextError(
            "TaskCenter attempt metadata does not match the persisted task row."
        )

    attempt = runtime.attempt_store.get(attempt_id)
    if attempt is None:
        raise AttemptSubmissionContextError(
            f"Attempt {attempt_id!r} was not found."
        )

    episode = runtime.episode_store.get(attempt.episode_id)
    if episode is None:
        raise AttemptSubmissionContextError(
            f"Episode {attempt.episode_id!r} was not found."
        )

    mission = runtime.mission_store.get(episode.mission_id)
    if mission is None:
        raise AttemptSubmissionContextError(
            f"Mission {episode.mission_id!r} was not found."
        )

    try:
        orchestrator = runtime.orchestrator_registry.get_or_raise(attempt_id)
    except TaskCenterInvariantViolation as exc:
        raise AttemptSubmissionContextError(str(exc)) from exc

    return AttemptSubmissionContext(
        task_center_task_id=task_id,
        task=task,
        attempt=attempt,
        episode=episode,
        mission=mission,
        runtime=runtime,
        orchestrator=orchestrator,
    )
