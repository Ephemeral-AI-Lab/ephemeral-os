"""Workflow persistence store. Returns frozen DTOs."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from db.models.workflow import WorkflowRecord
from db.stores.base import SyncStoreMixin
from workflow._core.state import Workflow, WorkflowStatus


class WorkflowStore(SyncStoreMixin):
    """CRUD for Workflow. Returns frozen Workflow DTOs."""

    def insert(
        self,
        *,
        request_id: str,
        parent_task_id: str,
        workflow_goal: str,
    ) -> Workflow:
        with self._sf() as db:
            now = datetime.now(UTC)
            record = WorkflowRecord(
                id=str(uuid.uuid4()),
                request_id=request_id,
                parent_task_id=parent_task_id,
                goal=workflow_goal,
                status=WorkflowStatus.OPEN.value,
                iteration_ids=[],
                outcomes=None,
                created_at=now,
                updated_at=now,
            )
            db.add(record)
            db.commit()
            db.refresh(record)
            return self._to_dto(record)

    def get(self, workflow_id: str) -> Workflow | None:
        with self._sf() as db:
            record = db.get(WorkflowRecord, workflow_id)
            return self._to_dto(record) if record is not None else None

    def append_iteration_id(
        self, workflow_id: str, iteration_id: str
    ) -> Workflow:
        with self._sf() as db:
            record = db.get(WorkflowRecord, workflow_id)
            if record is None:
                raise LookupError(f"Workflow {workflow_id!r} not found")
            ids = list(record.iteration_ids or [])
            ids.append(iteration_id)
            record.iteration_ids = ids
            db.commit()
            db.refresh(record)
            return self._to_dto(record)

    def set_status(
        self,
        workflow_id: str,
        *,
        status: WorkflowStatus,
        closed_at: datetime | None = None,
        outcomes: str | None = None,
    ) -> Workflow:
        with self._sf() as db:
            record = db.get(WorkflowRecord, workflow_id)
            if record is None:
                raise LookupError(f"Workflow {workflow_id!r} not found")
            record.status = status.value
            if closed_at is not None:
                record.closed_at = closed_at
            if outcomes is not None:
                record.outcomes = outcomes
            db.commit()
            db.refresh(record)
            return self._to_dto(record)

    def list_for_parent_task(self, parent_task_id: str) -> list[Workflow]:
        with self._sf() as db:
            q = (
                db.query(WorkflowRecord)
                .filter(WorkflowRecord.parent_task_id == parent_task_id)
                .order_by(WorkflowRecord.created_at.asc())
            )
            return [self._to_dto(r) for r in q.all()]

    def list_for_request(self, request_id: str) -> list[Workflow]:
        with self._sf() as db:
            q = (
                db.query(WorkflowRecord)
                .filter(
                    WorkflowRecord.request_id
                    == request_id
                )
                .order_by(WorkflowRecord.created_at.asc())
            )
            return [self._to_dto(r) for r in q.all()]

    def _to_dto(self, record: WorkflowRecord) -> Workflow:
        return Workflow(
            id=record.id,
            request_id=record.request_id,
            workflow_goal=record.goal,
            status=WorkflowStatus(record.status),
            iteration_ids=tuple(record.iteration_ids or ()),
            parent_task_id=record.parent_task_id,
            outcomes=record.outcomes,
            created_at=record.created_at,
            updated_at=record.updated_at,
            closed_at=record.closed_at,
        )
