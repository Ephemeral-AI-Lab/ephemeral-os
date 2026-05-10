"""LifecycleObserver — diffs stores on bus events."""

from __future__ import annotations

from collections.abc import Callable

from live_e2e.audit.bus import AuditEventBus
from live_e2e.audit.events import Event, EventType
from live_e2e.stores import TaskCenterStoreBundle
from task_center.domain import Attempt, Episode, Mission

__all__ = ["LifecycleObserver"]

_MISSION_EVENTS = frozenset(
    {
        EventType.MISSION_STARTED,
        EventType.MISSION_COMPLETED,
        EventType.MISSION_REQUESTED,
    }
)
_EPISODE_EVENTS = frozenset(
    {
        EventType.EPISODE_STARTED,
        EventType.EPISODE_COMPLETED,
        EventType.EPISODE_CONTINUATION_CREATED,
    }
)
_ATTEMPT_EVENTS = frozenset(
    {
        EventType.ATTEMPT_STARTED,
        EventType.ATTEMPT_PASSED,
        EventType.ATTEMPT_FAILED,
    }
)


class LifecycleObserver:
    """Subscribes to an AuditEventBus and keeps fresh snapshots of lifecycle DTOs."""

    def __init__(
        self,
        bus: AuditEventBus,
        stores: TaskCenterStoreBundle,
        *,
        task_center_run_id: str,
    ) -> None:
        self._stores = stores
        self._task_center_run_id = task_center_run_id
        self._missions: dict[str, Mission] = {}
        self._episodes: dict[str, Episode] = {}
        self._attempts: dict[str, Attempt] = {}
        self._unsubscribe: Callable[[], None] = bus.subscribe(self._on_event)

    def dispose(self) -> None:
        """Unsubscribe from the bus."""
        self._unsubscribe()

    # ------------------------------------------------------------------
    # Public read-only properties
    # ------------------------------------------------------------------

    @property
    def missions(self) -> dict[str, Mission]:
        return self._missions

    @property
    def episodes(self) -> dict[str, Episode]:
        return self._episodes

    @property
    def attempts(self) -> dict[str, Attempt]:
        return self._attempts

    def latest_mission_for(self, task_center_run_id: str) -> Mission | None:
        """Return the most recently observed mission for the given run id."""
        match: Mission | None = None
        for mission in self._missions.values():
            if mission.task_center_run_id == task_center_run_id:
                if match is None or mission.updated_at > match.updated_at:
                    match = mission
        return match

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_event(self, event: Event) -> None:
        if event.type in _MISSION_EVENTS:
            mission_id = event.node.mission_id
            if mission_id is not None:
                mission = self._stores.mission_store.get(mission_id)
                if mission is not None:
                    self._missions[mission_id] = mission

        elif event.type in _EPISODE_EVENTS:
            episode_id = event.node.episode_id
            if episode_id is not None:
                episode = self._stores.episode_store.get(episode_id)
                if episode is not None:
                    self._episodes[episode_id] = episode

        elif event.type in _ATTEMPT_EVENTS:
            attempt_id = event.node.attempt_id
            if attempt_id is not None:
                attempt = self._stores.attempt_store.get(attempt_id)
                if attempt is not None:
                    self._attempts[attempt_id] = attempt
