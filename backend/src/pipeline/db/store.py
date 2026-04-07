"""Database-backed pipeline store implementation."""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from pipeline.db.model import (
    PipelineCheckpointRecord,
    PipelineDefinitionRecord,
    PipelineRunRecord,
)
from pipeline.models import (
    PipelineCheckpoint,
    PipelineRun,
    PipelineRunStatus,
    StepRecord,
)
from pipeline.schema import PipelineConfig

logger = logging.getLogger(__name__)


def _step_record_to_dict(r: StepRecord) -> dict[str, Any]:
    return asdict(r)


def _step_record_from_dict(d: dict[str, Any]) -> StepRecord:
    return StepRecord(**d)


class DbPipelineStore:
    """PipelineStore backed by SQLAlchemy."""

    def __init__(self) -> None:
        self._session_factory: sessionmaker[Session] | None = None

    def initialize(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        logger.info("DbPipelineStore initialised")

    @property
    def is_available(self) -> bool:
        return self._session_factory is not None

    @property
    def _sf(self) -> sessionmaker[Session]:
        if self._session_factory is None:
            raise RuntimeError("DbPipelineStore not initialised")
        return self._session_factory

    # -- Pipeline definitions ---------------------------------------------------

    def save_pipeline(self, config: PipelineConfig) -> None:
        with self._sf() as db:
            record = db.get(PipelineDefinitionRecord, config.pipeline_id)
            data = config.model_dump(mode="json")
            if record is None:
                record = PipelineDefinitionRecord(
                    pipeline_id=config.pipeline_id,
                    name=config.name,
                    description=config.description,
                    version=config.version,
                    config_json=data,
                )
                db.add(record)
            else:
                record.name = config.name
                record.description = config.description
                record.version = config.version
                record.config_json = data
            db.commit()

    def get_pipeline(self, pipeline_id: str) -> PipelineConfig | None:
        with self._sf() as db:
            record = db.get(PipelineDefinitionRecord, pipeline_id)
            if record is None:
                return None
            return PipelineConfig.model_validate(record.config_json)

    def list_pipelines(self) -> list[PipelineConfig]:
        with self._sf() as db:
            records = db.query(PipelineDefinitionRecord).order_by(
                PipelineDefinitionRecord.updated_at.desc()
            ).all()
            return [PipelineConfig.model_validate(r.config_json) for r in records]

    def delete_pipeline(self, pipeline_id: str) -> bool:
        with self._sf() as db:
            record = db.get(PipelineDefinitionRecord, pipeline_id)
            if record is None:
                return False
            db.delete(record)
            db.commit()
            return True

    # -- Pipeline runs ----------------------------------------------------------

    async def create_run(self, run: PipelineRun) -> None:
        with self._sf() as db:
            record = PipelineRunRecord(
                run_id=run.run_id,
                pipeline_id=run.pipeline_id,
                goal=run.goal,
                status=run.status,
                current_step=run.current_step,
                completed_steps=run.completed_steps,
                context_map=run.context_map,
                step_records=[_step_record_to_dict(r) for r in run.step_records],
                error=run.error,
                attempt_number=run.attempt_number,
                resumed_from_checkpoint=run.resumed_from_checkpoint,
                started_at=run.started_at,
                finished_at=run.finished_at,
            )
            db.add(record)
            db.commit()

    async def get_run(self, run_id: str) -> PipelineRun | None:
        with self._sf() as db:
            record = db.get(PipelineRunRecord, run_id)
            if record is None:
                return None
            return PipelineRun(
                run_id=record.run_id,
                pipeline_id=record.pipeline_id,
                goal=record.goal or "",
                status=record.status or PipelineRunStatus.PENDING,
                current_step=record.current_step,
                completed_steps=record.completed_steps or [],
                context_map=record.context_map or {},
                step_records=[_step_record_from_dict(d) for d in (record.step_records or [])],
                error=record.error,
                attempt_number=record.attempt_number or 1,
                resumed_from_checkpoint=record.resumed_from_checkpoint,
                started_at=record.started_at,
                finished_at=record.finished_at,
            )

    async def update_run(self, run: PipelineRun) -> None:
        with self._sf() as db:
            record = db.get(PipelineRunRecord, run.run_id)
            if record is None:
                logger.warning("Cannot update non-existent run %s", run.run_id)
                return
            record.status = run.status
            record.current_step = run.current_step
            record.completed_steps = run.completed_steps
            record.context_map = run.context_map
            record.step_records = [_step_record_to_dict(r) for r in run.step_records]
            record.error = run.error
            record.attempt_number = run.attempt_number
            record.resumed_from_checkpoint = run.resumed_from_checkpoint
            record.started_at = run.started_at
            record.finished_at = run.finished_at
            db.commit()

    async def list_runs(self, pipeline_id: str | None = None) -> list[PipelineRun]:
        with self._sf() as db:
            query = db.query(PipelineRunRecord)
            if pipeline_id:
                query = query.filter(PipelineRunRecord.pipeline_id == pipeline_id)
            query = query.order_by(PipelineRunRecord.created_at.desc())
            results: list[PipelineRun] = []
            for record in query.all():
                results.append(PipelineRun(
                    run_id=record.run_id,
                    pipeline_id=record.pipeline_id,
                    goal=record.goal or "",
                    status=record.status or PipelineRunStatus.PENDING,
                    current_step=record.current_step,
                    completed_steps=record.completed_steps or [],
                    context_map=record.context_map or {},
                    step_records=[_step_record_from_dict(d) for d in (record.step_records or [])],
                    error=record.error,
                    attempt_number=record.attempt_number or 1,
                    resumed_from_checkpoint=record.resumed_from_checkpoint,
                    started_at=record.started_at,
                    finished_at=record.finished_at,
                ))
            return results

    # -- Checkpoints ------------------------------------------------------------

    async def save_checkpoint(
        self, run: PipelineRun, checkpoint: PipelineCheckpoint
    ) -> None:
        with self._sf() as db:
            record = PipelineCheckpointRecord(
                checkpoint_id=checkpoint.checkpoint_id,
                run_id=checkpoint.run_id,
                step_name=checkpoint.step_name,
                step_index=checkpoint.step_index,
                context_map_snapshot=checkpoint.context_map_snapshot,
                completed_steps=checkpoint.completed_steps,
                step_records=[_step_record_to_dict(r) for r in checkpoint.step_records],
                created_at=checkpoint.created_at,
            )
            db.add(record)
            db.commit()

    async def get_checkpoint(
        self, run_id: str, checkpoint_id: str
    ) -> PipelineCheckpoint | None:
        with self._sf() as db:
            record = db.get(PipelineCheckpointRecord, checkpoint_id)
            if record is None or record.run_id != run_id:
                return None
            return PipelineCheckpoint(
                checkpoint_id=record.checkpoint_id,
                run_id=record.run_id,
                step_name=record.step_name,
                step_index=record.step_index,
                context_map_snapshot=record.context_map_snapshot or {},
                completed_steps=record.completed_steps or [],
                step_records=[_step_record_from_dict(d) for d in (record.step_records or [])],
                created_at=record.created_at or 0.0,
            )

    async def list_checkpoints(self, run_id: str) -> list[PipelineCheckpoint]:
        with self._sf() as db:
            records = (
                db.query(PipelineCheckpointRecord)
                .filter(PipelineCheckpointRecord.run_id == run_id)
                .order_by(PipelineCheckpointRecord.step_index)
                .all()
            )
            return [
                PipelineCheckpoint(
                    checkpoint_id=r.checkpoint_id,
                    run_id=r.run_id,
                    step_name=r.step_name,
                    step_index=r.step_index,
                    context_map_snapshot=r.context_map_snapshot or {},
                    completed_steps=r.completed_steps or [],
                    step_records=[_step_record_from_dict(d) for d in (r.step_records or [])],
                    created_at=r.created_at or 0.0,
                )
                for r in records
            ]
