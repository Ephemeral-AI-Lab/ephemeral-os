"""Mission CRUD — repository facade over :class:`MissionStoreProtocol`.

Carved out of :class:`MissionHandler` so the mission boundary is no longer
one 281-line class doing four verbs. Owns:

- Mission insertion (``create``).
- Mission lookup with hard-fail (``require``).
- Episode-id append onto the mission row (``append_episode_id``).
- Mission status transitions on closure (``set_status``).
- :class:`MissionClosureReport` synthesis bundled with the closure write
  (``close``).

The repository deliberately does NOT publish the close-report sink; the
caller (:class:`MissionHandler`) handles delivery so the sink stays a
single seam.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from task_center.exceptions import TaskCenterInvariantViolation
from task_center.invariants import (
    assert_episode_id_unique_in_mission,
    assert_mission_open,
)
from task_center.mission.state import (
    Mission,
    MissionClosureReport,
    MissionStatus,
)
from task_center.persistence import MissionStoreProtocol


class MissionRepository:
    """CRUD + closure helpers for :class:`Mission` records."""

    def __init__(self, mission_store: MissionStoreProtocol) -> None:
        self._mission_store = mission_store

    def create(
        self,
        *,
        task_center_run_id: str,
        requested_by_task_id: str,
        goal: str,
    ) -> Mission:
        return self._mission_store.insert(
            task_center_run_id=task_center_run_id,
            requested_by_task_id=requested_by_task_id,
            goal=goal,
        )

    def get(self, mission_id: str) -> Mission | None:
        return self._mission_store.get(mission_id)

    def require(self, mission_id: str) -> Mission:
        mission = self.get(mission_id)
        if mission is None:
            raise TaskCenterInvariantViolation(
                f"Mission {mission_id!r} not found"
            )
        return mission

    def append_episode_id(
        self, mission: Mission, episode_id: str
    ) -> Mission:
        assert_episode_id_unique_in_mission(mission, episode_id)
        return self._mission_store.append_episode_id(mission.id, episode_id)

    def close(
        self,
        *,
        mission_id: str,
        succeeded: bool,
        final_episode_id: str,
        final_attempt_id: str | None,
    ) -> tuple[Mission, MissionClosureReport]:
        """Close the mission and synthesise its :class:`MissionClosureReport`."""
        mission = self.require(mission_id)
        assert_mission_open(mission)
        outcome_label: Literal["success", "failed"] = (
            "success" if succeeded else "failed"
        )
        report = MissionClosureReport(
            mission_id=mission_id,
            requested_by_task_id=mission.requested_by_task_id,
            outcome=outcome_label,
            final_episode_id=final_episode_id,
            final_attempt_id=final_attempt_id,
        )
        status = (
            MissionStatus.SUCCEEDED
            if succeeded
            else MissionStatus.FAILED
        )
        updated = self._mission_store.set_status(
            mission_id,
            status=status,
            final_outcome=report.to_final_outcome(),
            closed_at=datetime.now(UTC),
        )
        return updated, report


__all__ = ["MissionRepository"]
