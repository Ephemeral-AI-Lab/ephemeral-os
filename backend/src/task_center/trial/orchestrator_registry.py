"""Process-local registry for active trial orchestrators.

The registry stores objects implementing :class:`RegisteredTrialOrchestrator`
(structurally satisfied by :class:`TrialOrchestrator`). Using the protocol
instead of the concrete class lets this module import at runtime without
pulling in :mod:`task_center.trial.orchestrator`, which itself depends on
this registry — the cycle is broken at the type level.
"""

from __future__ import annotations

from task_center._core.types import TaskCenterInvariantViolation
from task_center._core.types import RegisteredTrialOrchestrator


class TrialOrchestratorRegistry:
    """In-memory lookup by Trial id."""

    def __init__(self) -> None:
        self._by_trial_id: dict[str, RegisteredTrialOrchestrator] = {}

    def register(self, orchestrator: RegisteredTrialOrchestrator) -> None:
        trial_id = orchestrator.trial_id
        current = self._by_trial_id.get(trial_id)
        if current is not None and current is not orchestrator:
            raise TaskCenterInvariantViolation(
                f"TrialOrchestrator already registered for trial "
                f"{trial_id!r}"
            )
        self._by_trial_id[trial_id] = orchestrator

    def get(self, trial_id: str) -> RegisteredTrialOrchestrator | None:
        return self._by_trial_id.get(trial_id)

    def get_or_raise(
        self, trial_id: str
    ) -> RegisteredTrialOrchestrator:
        orchestrator = self.get(trial_id)
        if orchestrator is None:
            raise TaskCenterInvariantViolation(
                f"No active TrialOrchestrator for trial "
                f"{trial_id!r}"
            )
        return orchestrator

    def deregister(self, trial_id: str) -> None:
        self._by_trial_id.pop(trial_id, None)
