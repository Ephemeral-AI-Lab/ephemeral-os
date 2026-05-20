"""Runtime DI bundle (:class:`AttemptDeps`) + lifecycle target protocol.

Includes :class:`AttemptDeps` (the launcher/orchestrator/store seam threaded
into every spawn) plus :class:`LifecycleTarget` and
:class:`GeneratorTaskLifecycle`, which expose a uniform parent-task waiter
surface for both entry-mode (``EntryTaskController``) and attempt-mode
(generator inside an :class:`AttemptOrchestrator`).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from audit.base import AuditSink, NoopAuditSink

from task_center.attempt.state import Attempt
from task_center._core.primitives import TaskCenterLifecycleConfig
from task_center.iteration import IterationManagerRegistry
from task_center._core.primitives import TaskCenterInvariantViolation
from task_center._core.persistence import (
    AttemptStoreProtocol,
    IterationStoreProtocol,
    GoalStoreProtocol,
    TaskStoreProtocol,
)
from task_center.task_state import TaskCenterTaskRole, TaskCenterTaskStatus

if TYPE_CHECKING:
    from task_center.agent_launch.composer import AgentEntryComposer
    from task_center.attempt.launch import EphemeralAttemptAgentLauncher
    from task_center.attempt.orchestrator_registry import (
        AttemptOrchestratorRegistry,
        RegisteredAttemptOrchestrator,
    )
    from task_center.entry import EntryTaskController
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
      around the per-agent role prose; ``None`` for entry_executor (2-row
      launch shape).
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
    manager_registry: IterationManagerRegistry | None = None
    lifecycle_config: TaskCenterLifecycleConfig = field(default_factory=TaskCenterLifecycleConfig)
    # When set, orchestrator + dispatcher route launches through the composer
    # to obtain a rendered context envelope + selected agent definition.
    # Optional so existing tests can continue without composer wiring.
    composer: AgentEntryComposer | None = None
    # Lifecycle controller for the top-level entry executor. ``None`` for
    # delegated-only runtimes.
    # The close-report router and launcher use this to dispatch lifecycle
    # events for entry tasks whose ``task_center_attempt_id`` is None.
    entry_task_controller: EntryTaskController | None = None
    audit_sink: AuditSink = field(default_factory=NoopAuditSink)

    def run_id_for_attempt(self, attempt: Attempt) -> str:
        iteration = self.iteration_store.get(attempt.iteration_id)
        if iteration is None:
            raise TaskCenterInvariantViolation(
                f"Iteration {attempt.iteration_id!r} not found for "
                f"Attempt {attempt.id!r}"
            )
        goal = self.goal_store.get(iteration.goal_id)
        if goal is None:
            raise TaskCenterInvariantViolation(
                f"Goal {iteration.goal_id!r} not "
                f"found for Iteration {iteration.id!r}"
            )
        return goal.task_center_run_id

    def require_composer(self) -> AgentEntryComposer:
        if self.composer is None:
            raise TaskCenterInvariantViolation(
                "AttemptDeps requires an AgentEntryComposer for harness "
                "agent launches; none was wired."
            )
        return self.composer

    def lifecycle_target_for(
        self, *, task_id: str, attempt_id: str | None
    ) -> LifecycleTarget | None:
        """Return the :class:`LifecycleTarget` for one parent task.

        For entry-mode (``attempt_id is None``), returns the
        :class:`EntryTaskController` bound to *task_id* if any. For
        attempt-mode, wraps the active orchestrator in a
        :class:`GeneratorTaskLifecycle`. Returns ``None`` when no target is
        registered — callers decide whether that's a hard error.
        """
        if attempt_id is None:
            controller = self.entry_task_controller
            if controller is None or controller.task_id != task_id:
                return None
            return controller
        return GeneratorTaskLifecycle(
            task_id=task_id,
            attempt_id=attempt_id,
            task_store=self.task_store,
            orchestrator_lookup=self.orchestrator_registry.get,
        )


# ---- LifecycleTarget seam (polymorphic parent-task owner) ------------------


class LifecycleTarget(Protocol):
    """Lifecycle owner for one parent task waiting on a delegated goal.

    Implementations: :class:`EntryTaskController` (entry mode), and
    :class:`GeneratorTaskLifecycle` (attempt mode).
    """

    task_id: str

    def apply_goal_closure_report(
        self, report: GoalClosureReport
    ) -> None: ...

    def mark_waiting_goal(
        self,
        *,
        delegated_goal_id: str,
        delegated_iteration_id: str,
        delegated_attempt_id: str,
        goal: str,
    ) -> None: ...

    def restore_running_after_failed_goal_start(self) -> None: ...


@dataclass(frozen=True, slots=True)
class GeneratorTaskLifecycle:
    """:class:`LifecycleTarget` for a generator task inside a attempt."""

    task_id: str
    attempt_id: str
    task_store: TaskStoreProtocol
    orchestrator_lookup: Callable[[str], RegisteredAttemptOrchestrator | None]

    def apply_goal_closure_report(
        self, report: GoalClosureReport
    ) -> None:
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
