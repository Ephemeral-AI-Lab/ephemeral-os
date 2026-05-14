"""Runtime Protocols for cycle-safe collaboration between lifecycle modules.

The TaskCenter package has unavoidable structural couplings (orchestrator ↔
registry, manager ↔ orchestrator, lifecycle target ↔ orchestrator). Before
this module they were brokered by ``TYPE_CHECKING``-guarded imports —
working but flagged in §3.1 of the architecture review as "acyclic by
runtime trickery, not by design."

The Protocols here are the design-time seam. Each captures the narrow
slice of an interface that a downstream collaborator observes; the
concrete classes (``AttemptOrchestrator``, ``EpisodeManager``) implement
these structurally. Registries and adapters can then depend on the
protocol module at runtime without ever importing the concrete class.

Adding a new collaborator slice means adding a Protocol here, not a new
``TYPE_CHECKING`` block in five files.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:  # pragma: no cover - typing-only
    from task_center.mission.state import MissionClosureReport


class RegisteredAttemptOrchestrator(Protocol):
    """The slice of :class:`AttemptOrchestrator` observed by collaborators.

    Used by:

    - :class:`AttemptOrchestratorRegistry` (read ``attempt_id`` to index).
    - :class:`EpisodeManager` (call ``start`` after factory construction).
    - :class:`GeneratorTaskLifecycle` (call
      ``apply_mission_closure_report`` on resume).
    """

    @property
    def attempt_id(self) -> str: ...

    def start(self) -> None: ...

    def apply_mission_closure_report(
        self, report: MissionClosureReport
    ) -> None: ...


class RegisteredEpisodeManager(Protocol):
    """The slice of :class:`EpisodeManager` observed by the registry.

    The registry indexes managers by ``episode_id`` and otherwise stores
    them opaquely. Lifecycle behaviour is invoked through the concrete
    class only; the registry's contract is read-only.
    """

    episode_id: str


__all__ = [
    "RegisteredAttemptOrchestrator",
    "RegisteredEpisodeManager",
]
