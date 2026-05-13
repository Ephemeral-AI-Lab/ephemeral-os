"""Episode persistence store. Returns frozen DTOs."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from db.models.episode import EpisodeRecord
from db.stores.base import SyncStoreMixin
from task_center.domain import (
    Episode,
    EpisodeCreationReason,
    EpisodeStatus,
)


class EpisodeStore(SyncStoreMixin):
    """CRUD for Episode. Returns frozen Episode DTOs."""

    def insert(
        self,
        *,
        mission_id: str,
        sequence_no: int,
        creation_reason: EpisodeCreationReason,
        goal: str,
        attempt_budget: int,
    ) -> Episode:
        with self._sf() as db:
            now = datetime.now(UTC)
            record = EpisodeRecord(
                id=str(uuid.uuid4()),
                mission_id=mission_id,
                sequence_no=sequence_no,
                creation_reason=creation_reason.value,
                goal=goal,
                attempt_budget=attempt_budget,
                status=EpisodeStatus.OPEN.value,
                attempt_ids=[],
                continuation_goal=None,
                created_at=now,
                updated_at=now,
            )
            db.add(record)
            db.commit()
            db.refresh(record)
            return self._to_dto(record)

    def get(self, episode_id: str) -> Episode | None:
        with self._sf() as db:
            record = db.get(EpisodeRecord, episode_id)
            return self._to_dto(record) if record is not None else None

    def append_attempt_id(self, episode_id: str, attempt_id: str) -> Episode:
        with self._sf() as db:
            record = db.get(EpisodeRecord, episode_id)
            if record is None:
                raise LookupError(f"Episode {episode_id!r} not found")
            ids = list(record.attempt_ids or [])
            ids.append(attempt_id)
            record.attempt_ids = ids
            db.commit()
            db.refresh(record)
            return self._to_dto(record)

    def set_continuation_goal(
        self, episode_id: str, continuation_goal: str | None
    ) -> Episode:
        with self._sf() as db:
            record = db.get(EpisodeRecord, episode_id)
            if record is None:
                raise LookupError(f"Episode {episode_id!r} not found")
            record.continuation_goal = continuation_goal
            db.commit()
            db.refresh(record)
            return self._to_dto(record)

    def set_status(
        self,
        episode_id: str,
        *,
        status: EpisodeStatus,
        closed_at: datetime | None = None,
    ) -> Episode:
        with self._sf() as db:
            record = db.get(EpisodeRecord, episode_id)
            if record is None:
                raise LookupError(f"Episode {episode_id!r} not found")
            record.status = status.value
            if closed_at is not None:
                record.closed_at = closed_at
            db.commit()
            db.refresh(record)
            return self._to_dto(record)

    def list_for_mission(
        self, mission_id: str
    ) -> list[Episode]:
        """Ordered by sequence_no ascending."""
        with self._sf() as db:
            q = (
                db.query(EpisodeRecord)
                .filter(
                    EpisodeRecord.mission_id
                    == mission_id
                )
                .order_by(EpisodeRecord.sequence_no.asc())
            )
            return [self._to_dto(r) for r in q.all()]

    def get_by_sequence(
        self, *, mission_id: str, sequence_no: int
    ) -> Episode | None:
        with self._sf() as db:
            record = (
                db.query(EpisodeRecord)
                .filter(
                    EpisodeRecord.mission_id
                    == mission_id,
                    EpisodeRecord.sequence_no == sequence_no,
                )
                .first()
            )
            return self._to_dto(record) if record is not None else None

    def close_succeeded(
        self,
        episode_id: str,
        *,
        task_specification: str,
        task_summary: str,
        closed_at: datetime | None = None,
    ) -> Episode:
        """Atomically transition to SUCCEEDED + write denormalized fields.

        All three writes (status, task_specification, task_summary) happen
        inside one ``db.commit()`` so a mid-write crash leaves the row
        untouched. Continuation-segment spawn happens *after* this returns
        and reads the just-closed row's denormalized fields.
        """
        with self._sf() as db:
            record = db.get(EpisodeRecord, episode_id)
            if record is None:
                raise LookupError(f"Episode {episode_id!r} not found")
            record.status = EpisodeStatus.SUCCEEDED.value
            record.task_specification = task_specification
            record.task_summary = task_summary
            if closed_at is not None:
                record.closed_at = closed_at
            db.commit()
            db.refresh(record)
            return self._to_dto(record)

    def _to_dto(self, record: EpisodeRecord) -> Episode:
        return Episode(
            id=record.id,
            mission_id=record.mission_id,
            sequence_no=record.sequence_no,
            creation_reason=EpisodeCreationReason(record.creation_reason),
            goal=record.goal,
            attempt_budget=record.attempt_budget,
            status=EpisodeStatus(record.status),
            attempt_ids=tuple(record.attempt_ids or ()),
            continuation_goal=record.continuation_goal,
            created_at=record.created_at,
            updated_at=record.updated_at,
            closed_at=record.closed_at,
            task_specification=record.task_specification,
            task_summary=record.task_summary,
        )
