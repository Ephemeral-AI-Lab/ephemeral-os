"""Attempt persistence store. Returns frozen DTOs."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from db.models.attempt import AttemptRecord
from db.stores.base import SyncStoreMixin
from task_center._core.state import (
    Attempt,
    AttemptFailReason,
    AttemptStage,
    AttemptStatus,
)


class AttemptStore(SyncStoreMixin):
    """CRUD for Attempt. Returns frozen Attempt DTOs."""

    def insert(
        self, *, iteration_id: str, attempt_sequence_no: int
    ) -> Attempt:
        with self._sf() as db:
            now = datetime.now(UTC)
            record = AttemptRecord(
                id=str(uuid.uuid4()),
                iteration_id=iteration_id,
                attempt_sequence_no=attempt_sequence_no,
                stage=AttemptStage.PLAN.value,
                status=AttemptStatus.RUNNING.value,
                planner_task_id=None,
                generator_task_ids=[],
                reducer_task_ids=[],
                deferred_goal=None,
                fail_reason=None,
                created_at=now,
                updated_at=now,
            )
            db.add(record)
            db.commit()
            db.refresh(record)
            return self._to_dto(record)

    def get(self, attempt_id: str) -> Attempt | None:
        with self._sf() as db:
            record = db.get(AttemptRecord, attempt_id)
            return self._to_dto(record) if record is not None else None

    def set_planner_task_id(
        self, attempt_id: str, planner_task_id: str
    ) -> Attempt:
        with self._sf() as db:
            record = db.get(AttemptRecord, attempt_id)
            if record is None:
                raise LookupError(f"Attempt {attempt_id!r} not found")
            record.planner_task_id = planner_task_id
            db.commit()
            db.refresh(record)
            return self._to_dto(record)

    def set_deferred_goal(
        self,
        attempt_id: str,
        *,
        deferred_goal_for_next_iteration: str | None,
    ) -> Attempt:
        with self._sf() as db:
            record = db.get(AttemptRecord, attempt_id)
            if record is None:
                raise LookupError(f"Attempt {attempt_id!r} not found")
            record.deferred_goal = deferred_goal_for_next_iteration
            db.commit()
            db.refresh(record)
            return self._to_dto(record)

    def set_generator_task_ids(
        self, attempt_id: str, generator_task_ids: list[str]
    ) -> Attempt:
        with self._sf() as db:
            record = db.get(AttemptRecord, attempt_id)
            if record is None:
                raise LookupError(f"Attempt {attempt_id!r} not found")
            record.generator_task_ids = list(generator_task_ids)
            db.commit()
            db.refresh(record)
            return self._to_dto(record)

    def set_reducer_task_ids(
        self, attempt_id: str, reducer_task_ids: list[str]
    ) -> Attempt:
        with self._sf() as db:
            record = db.get(AttemptRecord, attempt_id)
            if record is None:
                raise LookupError(f"Attempt {attempt_id!r} not found")
            record.reducer_task_ids = list(reducer_task_ids)
            db.commit()
            db.refresh(record)
            return self._to_dto(record)

    def set_stage(
        self, attempt_id: str, stage: AttemptStage
    ) -> Attempt:
        with self._sf() as db:
            record = db.get(AttemptRecord, attempt_id)
            if record is None:
                raise LookupError(f"Attempt {attempt_id!r} not found")
            record.stage = stage.value
            db.commit()
            db.refresh(record)
            return self._to_dto(record)

    def close(
        self,
        attempt_id: str,
        *,
        status: AttemptStatus,
        fail_reason: AttemptFailReason | None,
        closed_at: datetime | None = None,
    ) -> Attempt:
        with self._sf() as db:
            record = db.get(AttemptRecord, attempt_id)
            if record is None:
                raise LookupError(f"Attempt {attempt_id!r} not found")
            record.stage = AttemptStage.CLOSED.value
            record.status = status.value
            record.fail_reason = fail_reason.value if fail_reason is not None else None
            record.closed_at = closed_at if closed_at is not None else datetime.now(UTC)
            db.commit()
            db.refresh(record)
            return self._to_dto(record)

    def list_for_iteration(self, iteration_id: str) -> list[Attempt]:
        """Ordered by attempt_sequence_no ascending."""
        with self._sf() as db:
            q = (
                db.query(AttemptRecord)
                .filter(AttemptRecord.iteration_id == iteration_id)
                .order_by(AttemptRecord.attempt_sequence_no.asc())
            )
            return [self._to_dto(r) for r in q.all()]

    def get_by_sequence(
        self, *, iteration_id: str, attempt_sequence_no: int
    ) -> Attempt | None:
        with self._sf() as db:
            record = (
                db.query(AttemptRecord)
                .filter(
                    AttemptRecord.iteration_id == iteration_id,
                    AttemptRecord.attempt_sequence_no == attempt_sequence_no,
                )
                .first()
            )
            return self._to_dto(record) if record is not None else None

    def _to_dto(self, record: AttemptRecord) -> Attempt:
        return Attempt(
            id=record.id,
            iteration_id=record.iteration_id,
            attempt_sequence_no=record.attempt_sequence_no,
            stage=AttemptStage(record.stage),
            status=AttemptStatus(record.status),
            planner_task_id=record.planner_task_id,
            generator_task_ids=tuple(record.generator_task_ids or ()),
            reducer_task_ids=tuple(record.reducer_task_ids or ()),
            deferred_goal_for_next_iteration=record.deferred_goal,
            fail_reason=(
                AttemptFailReason(record.fail_reason)
                if record.fail_reason is not None
                else None
            ),
            created_at=record.created_at,
            updated_at=record.updated_at,
            closed_at=record.closed_at,
        )
