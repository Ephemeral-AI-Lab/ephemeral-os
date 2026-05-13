"""Mission persistence store. Returns frozen DTOs."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from db.models.mission import MissionRecord
from db.stores.base import SyncStoreMixin
from task_center.domain import (
    Mission,
    MissionStatus,
)


class MissionStore(SyncStoreMixin):
    """CRUD for Mission. Returns frozen Mission DTOs."""

    def insert(
        self,
        *,
        task_center_run_id: str,
        requested_by_task_id: str,
        goal: str,
    ) -> Mission:
        with self._sf() as db:
            now = datetime.now(UTC)
            record = MissionRecord(
                id=str(uuid.uuid4()),
                task_center_run_id=task_center_run_id,
                requested_by_task_id=requested_by_task_id,
                goal=goal,
                status=MissionStatus.OPEN.value,
                episode_ids=[],
                final_outcome=None,
                created_at=now,
                updated_at=now,
            )
            db.add(record)
            db.commit()
            db.refresh(record)
            return self._to_dto(record)

    def get(self, mission_id: str) -> Mission | None:
        with self._sf() as db:
            record = db.get(MissionRecord, mission_id)
            return self._to_dto(record) if record is not None else None

    def append_episode_id(
        self, mission_id: str, episode_id: str
    ) -> Mission:
        with self._sf() as db:
            record = db.get(MissionRecord, mission_id)
            if record is None:
                raise LookupError(f"Mission {mission_id!r} not found")
            ids = list(record.episode_ids or [])
            ids.append(episode_id)
            record.episode_ids = ids
            db.commit()
            db.refresh(record)
            return self._to_dto(record)

    def set_status(
        self,
        mission_id: str,
        *,
        status: MissionStatus,
        final_outcome: dict | None,
        closed_at: datetime | None = None,
    ) -> Mission:
        with self._sf() as db:
            record = db.get(MissionRecord, mission_id)
            if record is None:
                raise LookupError(f"Mission {mission_id!r} not found")
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
                db.query(MissionRecord)
                .filter(
                    MissionRecord.requested_by_task_id
                    == requested_by_task_id
                )
                .order_by(MissionRecord.created_at.asc())
            )
            return [self._to_dto(r) for r in q.all()]

    def list_for_run(
        self, task_center_run_id: str
    ) -> list[Mission]:
        with self._sf() as db:
            q = (
                db.query(MissionRecord)
                .filter(
                    MissionRecord.task_center_run_id
                    == task_center_run_id
                )
                .order_by(MissionRecord.created_at.asc())
            )
            return [self._to_dto(r) for r in q.all()]

    def _to_dto(self, record: MissionRecord) -> Mission:
        return Mission(
            id=record.id,
            task_center_run_id=record.task_center_run_id,
            requested_by_task_id=record.requested_by_task_id,
            goal=record.goal,
            status=MissionStatus(record.status),
            episode_ids=tuple(record.episode_ids or ()),
            final_outcome=record.final_outcome,
            created_at=record.created_at,
            updated_at=record.updated_at,
            closed_at=record.closed_at,
        )
