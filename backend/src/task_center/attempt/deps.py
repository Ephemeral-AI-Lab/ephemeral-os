"""Runtime DI bundle (:class:`AttemptDeps`) plus delegated-goal parent tasks.

:class:`AttemptDeps` threads stores, orchestration, launch, and audit concerns
into every attempt-scoped spawn. :class:`AttemptDelegatedGoalParentTask`
owns the parent generator-task transitions while a child goal is running.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from audit.base import AuditSink, NoopAuditSink

from task_center.attempt.state import Attempt
from task_center._core.primitives import TaskCenterLifecycleConfig
from task_center.iteration import OpenIterationCoordinatorRegistry
from task_center._core.primitives import TaskCenterInvariantViolation
from task_center._core.persistence import (
    AttemptStoreProtocol,
    IterationStoreProtocol,
    GoalStoreProtocol,
    TaskStoreProtocol,
)
from task_center._core.task_state import TaskCenterTaskRole, TaskCenterTaskStatus

if TYPE_CHECKING:
    from task_center.agent_launch.composer import AgentEntryComposer
    from task_center.attempt.launch import EphemeralAttemptAgentLauncher
    from task_center.attempt.orchestrator_registry import (
        AttemptOrchestratorRegistry,
        RegisteredAttemptOrchestrator,
    )
    from task_center.goal.state import GoalClosureReport
    from agents import AgentDefinition


@dataclass(frozen=True, slots=True)
class AgentLaunch:
    """Launch descriptor for one harness agent run.

    The launch carries up to three user-message payloads matching the wire
    shape composed by :class:`AgentEntryComposer`:

    * ``context`` — ``<context>...</context>`` envelope around rendered
      packet blocks. Persisted into the task row for traceability.
    * ``task_guidance`` — ``<Task Guidance>...</Task Guidance>`` envelope
      around the per-agent role prose.
    * ``skill`` — row-4 ``Load skill:`` + ``<terminal_tool_selection>``
      body; ``None`` when the agent declares no skill.
    """

    task_id: str
    task_center_run_id: str
    attempt_id: str | None
    role: TaskCenterTaskRole
    agent_name: str
    context: str
    task_guidance: str | None
    needs: tuple[str, ...]
    agent_def: AgentDefinition | None = None
    context_packet_id: str | None = None
    goal_id: str | None = None
    skill: str | None = None


@dataclass(frozen=True, slots=True)
class AttemptDeps:
    goal_store: GoalStoreProtocol
    iteration_store: IterationStoreProtocol
    attempt_store: AttemptStoreProtocol
    task_store: TaskStoreProtocol
    agent_launcher: EphemeralAttemptAgentLauncher
    orchestrator_registry: AttemptOrchestratorRegistry
    iteration_coordinators: OpenIterationCoordinatorRegistry | None = None
    lifecycle_config: TaskCenterLifecycleConfig = field(default_factory=TaskCenterLifecycleConfig)
    # When set, orchestrator + stage advancer route launches through the composer
    # to obtain a rendered context envelope + selected agent definition.
    # Optional so existing tests can continue without composer wiring.
    composer: AgentEntryComposer | None = None
    audit_sink: AuditSink = field(default_factory=NoopAuditSink)

    def run_id_for_attempt(self, attempt: Attempt) -> str:
        iteration = self.iteration_store.get(attempt.iteration_id)
        if iteration is None:
            raise TaskCenterInvariantViolation(
                f"Iteration {attempt.iteration_id!r} not found for Attempt {attempt.id!r}"
            )
        goal = self.goal_store.get(iteration.goal_id)
        if goal is None:
            raise TaskCenterInvariantViolation(
                f"Goal {iteration.goal_id!r} not found for Iteration {iteration.id!r}"
            )
        return goal.task_center_run_id

    def require_composer(self) -> AgentEntryComposer:
        if self.composer is None:
            raise TaskCenterInvariantViolation(
                "AttemptDeps requires an AgentEntryComposer for harness "
                "agent launches; none was wired."
            )
        return self.composer

    def parent_task_for_delegated_goal(
        self, *, task_id: str, attempt_id: str | None
    ) -> AttemptDelegatedGoalParentTask | None:
        """Return the parent generator task waiting on a child goal."""
        if attempt_id is None:
            return None
        return AttemptDelegatedGoalParentTask(
            task_id=task_id,
            attempt_id=attempt_id,
            task_store=self.task_store,
            orchestrator_lookup=self.orchestrator_registry.get,
        )


@dataclass(frozen=True, slots=True)
class AttemptDelegatedGoalParentTask:
    """Parent generator task waiting on a delegated child goal."""

    task_id: str
    attempt_id: str
    task_store: TaskStoreProtocol
    orchestrator_lookup: Callable[[str], RegisteredAttemptOrchestrator | None]

    def apply_goal_closure_report(self, report: GoalClosureReport) -> None:
        orchestrator = self.orchestrator_lookup(self.attempt_id)
        if orchestrator is None:
            raise TaskCenterInvariantViolation(
                f"Parent AttemptOrchestrator for attempt {self.attempt_id!r} is "
                "not registered; close-report delivery requires an active "
                "parent orchestrator."
            )
        orchestrator.apply_goal_closure_report(report)

    def mark_waiting_goal(
        self,
        *,
        delegated_goal_id: str,
        delegated_iteration_id: str,
        delegated_attempt_id: str,
        goal: str,
    ) -> None:
        summary = {
            "outcome": "goal_start",
            "summary": "Waiting on delegated goal solution.",
            "payload": {
                "goal_id": delegated_goal_id,
                "initial_iteration_id": delegated_iteration_id,
                "initial_attempt_id": delegated_attempt_id,
                "parent_attempt_id": self.attempt_id,
                "goal": goal,
            },
        }
        updated = self.task_store.set_task_status_if_current(
            self.task_id,
            expected_status=TaskCenterTaskStatus.RUNNING.value,
            status=TaskCenterTaskStatus.WAITING_GOAL.value,
            summary=summary,
        )
        if updated is None:
            raise TaskCenterInvariantViolation(
                f"TaskCenter task {self.task_id!r} was not running when the "
                "delegated goal start tried to mark it waiting."
            )

    def restore_running_after_failed_goal_start(self) -> None:
        self.task_store.set_task_status_if_current(
            self.task_id,
            expected_status=TaskCenterTaskStatus.WAITING_GOAL.value,
            status=TaskCenterTaskStatus.RUNNING.value,
        )
