"""Trial persistence store. Returns frozen DTOs."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from db.models.trial import TrialRecord
from db.stores.base import SyncStoreMixin
from task_center.trial.state import (
    Trial,
    TrialFailReason,
    TrialStage,
    TrialStatus,
)


class TrialStore(SyncStoreMixin):
    """CRUD for Trial. Returns frozen Trial DTOs."""

    def insert(
        self, *, iteration_id: str, trial_sequence_no: int
    ) -> Trial:
        with self._sf() as db:
            now = datetime.now(UTC)
            record = TrialRecord(
                id=str(uuid.uuid4()),
                iteration_id=iteration_id,
                trial_sequence_no=trial_sequence_no,
                stage=TrialStage.PLAN.value,
                status=TrialStatus.RUNNING.value,
                planner_task_id=None,
                task_specification=None,
                evaluation_criteria=[],
                generator_task_ids=[],
                evaluator_task_id=None,
                continuation_goal=None,
                fail_reason=None,
                created_at=now,
                updated_at=now,
            )
            db.add(record)
            db.commit()
            db.refresh(record)
            return self._to_dto(record)

    def get(self, trial_id: str) -> Trial | None:
        with self._sf() as db:
            record = db.get(TrialRecord, trial_id)
            return self._to_dto(record) if record is not None else None

    def set_planner_task_id(
        self, trial_id: str, planner_task_id: str
    ) -> Trial:
        with self._sf() as db:
            record = db.get(TrialRecord, trial_id)
            if record is None:
                raise LookupError(f"Trial {trial_id!r} not found")
            record.planner_task_id = planner_task_id
            db.commit()
            db.refresh(record)
            return self._to_dto(record)

    def set_plan_contract(
        self,
        trial_id: str,
        *,
        task_specification: str,
        evaluation_criteria: list[str],
        continuation_goal: str | None,
    ) -> Trial:
        with self._sf() as db:
            record = db.get(TrialRecord, trial_id)
            if record is None:
                raise LookupError(f"Trial {trial_id!r} not found")
            record.task_specification = task_specification
            record.evaluation_criteria = list(evaluation_criteria)
            record.continuation_goal = continuation_goal
            db.commit()
            db.refresh(record)
            return self._to_dto(record)

    def set_generator_task_ids(
        self, trial_id: str, task_ids: list[str]
    ) -> Trial:
        with self._sf() as db:
            record = db.get(TrialRecord, trial_id)
            if record is None:
                raise LookupError(f"Trial {trial_id!r} not found")
            record.generator_task_ids = list(task_ids)
            db.commit()
            db.refresh(record)
            return self._to_dto(record)

    def set_evaluator_task_id(
        self, trial_id: str, evaluator_task_id: str
    ) -> Trial:
        with self._sf() as db:
            record = db.get(TrialRecord, trial_id)
            if record is None:
                raise LookupError(f"Trial {trial_id!r} not found")
            record.evaluator_task_id = evaluator_task_id
            db.commit()
            db.refresh(record)
            return self._to_dto(record)

    def set_stage(
        self, trial_id: str, stage: TrialStage
    ) -> Trial:
        with self._sf() as db:
            record = db.get(TrialRecord, trial_id)
            if record is None:
                raise LookupError(f"Trial {trial_id!r} not found")
            record.stage = stage.value
            db.commit()
            db.refresh(record)
            return self._to_dto(record)

    def close(
        self,
        trial_id: str,
        *,
        status: TrialStatus,
        fail_reason: TrialFailReason | None,
        closed_at: datetime | None = None,
    ) -> Trial:
        with self._sf() as db:
            record = db.get(TrialRecord, trial_id)
            if record is None:
                raise LookupError(f"Trial {trial_id!r} not found")
            record.stage = TrialStage.CLOSED.value
            record.status = status.value
            record.fail_reason = fail_reason.value if fail_reason is not None else None
            record.closed_at = closed_at if closed_at is not None else datetime.now(UTC)
            db.commit()
            db.refresh(record)
            return self._to_dto(record)

    def list_for_iteration(self, iteration_id: str) -> list[Trial]:
        """Ordered by trial_sequence_no ascending."""
        with self._sf() as db:
            q = (
                db.query(TrialRecord)
                .filter(TrialRecord.iteration_id == iteration_id)
                .order_by(TrialRecord.trial_sequence_no.asc())
            )
            return [self._to_dto(r) for r in q.all()]

    def get_by_sequence(
        self, *, iteration_id: str, trial_sequence_no: int
    ) -> Trial | None:
        with self._sf() as db:
            record = (
                db.query(TrialRecord)
                .filter(
                    TrialRecord.iteration_id == iteration_id,
                    TrialRecord.trial_sequence_no == trial_sequence_no,
                )
                .first()
            )
            return self._to_dto(record) if record is not None else None

    def _to_dto(self, record: TrialRecord) -> Trial:
        return Trial(
            id=record.id,
            iteration_id=record.iteration_id,
            trial_sequence_no=record.trial_sequence_no,
            stage=TrialStage(record.stage),
            status=TrialStatus(record.status),
            planner_task_id=record.planner_task_id,
            task_specification=record.task_specification,
            evaluation_criteria=tuple(record.evaluation_criteria or ()),
            generator_task_ids=tuple(record.generator_task_ids or ()),
            evaluator_task_id=record.evaluator_task_id,
            continuation_goal=record.continuation_goal,
            fail_reason=(
                TrialFailReason(record.fail_reason)
                if record.fail_reason is not None
                else None
            ),
            created_at=record.created_at,
            updated_at=record.updated_at,
            closed_at=record.closed_at,
        )
