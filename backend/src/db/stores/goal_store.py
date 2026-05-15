"""Goal persistence store. Returns frozen DTOs."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from db.models.goal import GoalRecord
from db.stores.base import SyncStoreMixin
from task_center.mission.state import (
    Mission,
    MissionStatus,
)


class GoalStore(SyncStoreMixin):
    """CRUD for Goal. Returns frozen Mission DTOs."""

    def insert(
        self,
        *,
        task_center_run_id: str,
        requested_by_task_id: str,
        goal: str,
    ) -> Mission:
        with self._sf() as db:
            now = datetime.now(UTC)
            record = GoalRecord(
                id=str(uuid.uuid4()),
                task_center_run_id=task_center_run_id,
                requested_by_task_id=requested_by_task_id,
                goal=goal,
                status=MissionStatus.OPEN.value,
                iteration_ids=[],
                final_outcome=None,
                created_at=now,
                updated_at=now,
            )
            db.add(record)
            db.commit()
            db.refresh(record)
            return self._to_dto(record)

    def get(self, goal_id: str) -> Mission | None:
        with self._sf() as db:
            record = db.get(GoalRecord, goal_id)
            return self._to_dto(record) if record is not None else None

    def append_iteration_id(
        self, goal_id: str, iteration_id: str
    ) -> Mission:
        with self._sf() as db:
            record = db.get(GoalRecord, goal_id)
            if record is None:
                raise LookupError(f"Goal {goal_id!r} not found")
            ids = list(record.iteration_ids or [])
            ids.append(iteration_id)
            record.iteration_ids = ids
            db.commit()
            db.refresh(record)
            return self._to_dto(record)

    def set_status(
        self,
        goal_id: str,
        *,
        status: MissionStatus,
        final_outcome: dict | None,
        closed_at: datetime | None = None,
    ) -> Mission:
        with self._sf() as db:
            record = db.get(GoalRecord, goal_id)
            if record is None:
                raise LookupError(f"Goal {goal_id!r} not found")
            record.status = status.value
            record.final_outcome = final_outcome
            if closed_at is not None:
                record.closed_at = closed_at
            db.commit()
            db.refresh(record)
            return self._to_dto(record)

    def list_for_executor_task(
        self, requested_by_task_id: str
    ) -> list[Mission]:
        with self._sf() as db:
            q = (
                db.query(GoalRecord)
                .filter(
                    GoalRecord.requested_by_task_id
                    == requested_by_task_id
                )
                .order_by(GoalRecord.created_at.asc())
            )
            return [self._to_dto(r) for r in q.all()]

    def list_for_run(
        self, task_center_run_id: str
    ) -> list[Mission]:
        with self._sf() as db:
            q = (
                db.query(GoalRecord)
                .filter(
                    GoalRecord.task_center_run_id
                    == task_center_run_id
                )
                .order_by(GoalRecord.created_at.asc())
            )
            return [self._to_dto(r) for r in q.all()]

    def _to_dto(self, record: GoalRecord) -> Mission:
        return Mission(
            id=record.id,
            task_center_run_id=record.task_center_run_id,
            requested_by_task_id=record.requested_by_task_id,
            goal=record.goal,
            status=MissionStatus(record.status),
            episode_ids=tuple(record.iteration_ids or ()),
            final_outcome=record.final_outcome,
            created_at=record.created_at,
            updated_at=record.updated_at,
            closed_at=record.closed_at,
        )
