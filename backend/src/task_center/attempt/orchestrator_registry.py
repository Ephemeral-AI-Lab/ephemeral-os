"""Process-local registry for active harness attempt orchestrators."""

from __future__ import annotations

from typing import TYPE_CHECKING

from task_center.exceptions import TaskCenterInvariantViolation

if TYPE_CHECKING:
    from task_center.attempt.orchestrator import AttemptOrchestrator


class AttemptOrchestratorRegistry:
    """In-memory lookup by Attempt id."""

    def __init__(self) -> None:
        self._by_attempt_id: dict[str, AttemptOrchestrator] = {}

    def register(self, orchestrator: AttemptOrchestrator) -> None:
        attempt_id = orchestrator.attempt_id
        current = self._by_attempt_id.get(attempt_id)
        if current is not None and current is not orchestrator:
            raise TaskCenterInvariantViolation(
                f"AttemptOrchestrator already registered for attempt "
                f"{attempt_id!r}"
            )
        self._by_attempt_id[attempt_id] = orchestrator

    def get(self, attempt_id: str) -> AttemptOrchestrator | None:
        return self._by_attempt_id.get(attempt_id)

    def get_or_raise(self, attempt_id: str) -> AttemptOrchestrator:
        orchestrator = self.get(attempt_id)
        if orchestrator is None:
            raise TaskCenterInvariantViolation(
                f"No active AttemptOrchestrator for attempt "
                f"{attempt_id!r}"
            )
        return orchestrator

    def deregister(self, attempt_id: str) -> None:
        self._by_attempt_id.pop(attempt_id, None)
