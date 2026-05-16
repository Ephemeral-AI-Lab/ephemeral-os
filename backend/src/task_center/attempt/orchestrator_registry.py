"""Process-local registry for active attempt orchestrators.

The registry stores objects implementing :class:`RegisteredAttemptOrchestrator`
(structurally satisfied by :class:`AttemptOrchestrator`). Using the protocol
instead of the concrete class lets this module import at runtime without
pulling in :mod:`task_center.attempt.orchestrator`, which itself depends on
this registry — the cycle is broken at the type level.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from task_center._core.primitives import TaskCenterInvariantViolation

if TYPE_CHECKING:  # pragma: no cover - typing-only
    from task_center.goal.state import GoalClosureReport


class RegisteredAttemptOrchestrator(Protocol):
    """The slice of :class:`AttemptOrchestrator` observed by collaborators."""

    @property
    def attempt_id(self) -> str: ...

    def start(self) -> None: ...

    def apply_goal_closure_report(
        self, report: GoalClosureReport
    ) -> None: ...


class AttemptOrchestratorRegistry:
    """In-memory lookup by Attempt id."""

    def __init__(self) -> None:
        self._by_attempt_id: dict[str, RegisteredAttemptOrchestrator] = {}

    def register(self, orchestrator: RegisteredAttemptOrchestrator) -> None:
        attempt_id = orchestrator.attempt_id
        current = self._by_attempt_id.get(attempt_id)
        if current is not None and current is not orchestrator:
            raise TaskCenterInvariantViolation(
                f"AttemptOrchestrator already registered for attempt "
                f"{attempt_id!r}"
            )
        self._by_attempt_id[attempt_id] = orchestrator

    def get(self, attempt_id: str) -> RegisteredAttemptOrchestrator | None:
        return self._by_attempt_id.get(attempt_id)

    def get_or_raise(
        self, attempt_id: str
    ) -> RegisteredAttemptOrchestrator:
        orchestrator = self.get(attempt_id)
        if orchestrator is None:
            raise TaskCenterInvariantViolation(
                f"No active AttemptOrchestrator for attempt "
                f"{attempt_id!r}"
            )
        return orchestrator

    def deregister(self, attempt_id: str) -> None:
        self._by_attempt_id.pop(attempt_id, None)


__all__ = ["AttemptOrchestratorRegistry", "RegisteredAttemptOrchestrator"]
