"""TaskCenter request/run/task/graph persistence store."""

from __future__ import annotations

from datetime import UTC, datetime

from db.models.task_center import (
    TaskCenterGraphRecord,
    TaskCenterRequestRecord,
    TaskCenterRunRecord,
    TaskCenterTaskRecord,
)
from db.stores.base import SyncStoreMixin


def persisted_task_id(run_id: str, task_id: str) -> str:
    """Return the globally unique persisted id for an in-memory task id."""
    return f"{run_id}:{task_id}"


def _serialize_request(record: TaskCenterRequestRecord) -> dict:
    return {
        "id": record.id,
        "cwd": record.cwd,
        "sandbox_id": record.sandbox_id,
        "request_prompt": record.request_prompt,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
    }


def _serialize_run(record: TaskCenterRunRecord) -> dict:
    return {
        "id": record.id,
        "request_id": record.request_id,
        "root_task_id": record.root_task_id,
        "status": record.status,
        "started_at": record.started_at.isoformat() if record.started_at else None,
        "finished_at": record.finished_at.isoformat() if record.finished_at else None,
    }


def _serialize_task(record: TaskCenterTaskRecord) -> dict:
    return {
        "id": record.id,
        "run_id": record.run_id,
        "role": record.role,
        "title": record.title,
        "spec": record.spec,
        "status": record.status,
        "summary": record.summary,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
    }


def _serialize_graph(record: TaskCenterGraphRecord) -> dict:
    return {
        "run_id": record.run_id,
        "task_id": record.task_id,
        "parent_task_id": record.parent_task_id,
        "children_ids": record.children_ids or [],
        "evaluator_id": record.evaluator_id,
        "acceptance_criteria": record.acceptance_criteria,
        "handoff_note": record.handoff_note,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
    }


class TaskCenterStore(SyncStoreMixin):
    """CRUD operations for TaskCenter persistence."""

    def create_request(
        self,
        *,
        request_id: str,
        cwd: str,
        sandbox_id: str | None,
        request_prompt: str,
    ) -> TaskCenterRequestRecord:
        with self._sf() as db:
            now = datetime.now(UTC)
            record = TaskCenterRequestRecord(
                id=request_id,
                cwd=cwd,
                sandbox_id=sandbox_id,
                request_prompt=request_prompt,
                created_at=now,
                updated_at=now,
            )
            db.add(record)
            db.commit()
            db.refresh(record)
            return record

    def get_request(self, request_id: str) -> TaskCenterRequestRecord | None:
        with self._sf() as db:
            return db.get(TaskCenterRequestRecord, request_id)

    def list_requests(self, cwd: str | None = None, limit: int = 20) -> list[dict]:
        with self._sf() as db:
            q = db.query(TaskCenterRequestRecord)
            if cwd:
                q = q.filter(TaskCenterRequestRecord.cwd == cwd)
            q = q.order_by(TaskCenterRequestRecord.created_at.desc()).limit(limit)
            return [_serialize_request(record) for record in q.all()]

    def create_run(
        self,
        *,
        run_id: str,
        request_id: str,
    ) -> TaskCenterRunRecord:
        with self._sf() as db:
            record = TaskCenterRunRecord(
                id=run_id,
                request_id=request_id,
                status="running",
                started_at=datetime.now(UTC),
            )
            db.add(record)
            db.commit()
            db.refresh(record)
            return record

    def set_run_root(self, run_id: str, root_task_id: str) -> None:
        with self._sf() as db:
            record = db.get(TaskCenterRunRecord, run_id)
            if record is None:
                return
            record.root_task_id = root_task_id
            db.commit()

    def finish_run(self, run_id: str, status: str) -> None:
        with self._sf() as db:
            record = db.get(TaskCenterRunRecord, run_id)
            if record is None:
                return
            record.status = status
            record.finished_at = datetime.now(UTC)
            db.commit()

    def get_run(self, run_id: str) -> TaskCenterRunRecord | None:
        with self._sf() as db:
            return db.get(TaskCenterRunRecord, run_id)

    def list_runs_for_request(self, request_id: str, limit: int = 50) -> list[dict]:
        with self._sf() as db:
            q = (
                db.query(TaskCenterRunRecord)
                .filter(TaskCenterRunRecord.request_id == request_id)
                .order_by(TaskCenterRunRecord.started_at.desc())
                .limit(limit)
            )
            return [_serialize_run(record) for record in q.all()]

    def upsert_task(
        self,
        *,
        task_id: str,
        run_id: str,
        role: str,
        title: str,
        spec: str,
        status: str,
        summary: str | None,
    ) -> None:
        with self._sf() as db:
            now = datetime.now(UTC)
            record = db.get(TaskCenterTaskRecord, task_id)
            if record is None:
                record = TaskCenterTaskRecord(
                    id=task_id,
                    run_id=run_id,
                    role=role,
                    title=title,
                    spec=spec,
                    status=status,
                    summary=summary,
                    created_at=now,
                    updated_at=now,
                )
                db.add(record)
            else:
                record.role = role
                record.title = title
                record.spec = spec
                record.status = status
                record.summary = summary
                record.updated_at = now
            db.commit()

    def upsert_graph_node(
        self,
        *,
        run_id: str,
        task_id: str,
        parent_task_id: str | None,
        children_ids: list[str],
        evaluator_id: str | None,
        acceptance_criteria: str | None,
        handoff_note: str | None,
    ) -> None:
        with self._sf() as db:
            now = datetime.now(UTC)
            record = db.get(TaskCenterGraphRecord, task_id)
            if record is None:
                record = TaskCenterGraphRecord(
                    run_id=run_id,
                    task_id=task_id,
                    parent_task_id=parent_task_id,
                    children_ids=children_ids,
                    evaluator_id=evaluator_id,
                    acceptance_criteria=acceptance_criteria,
                    handoff_note=handoff_note,
                    created_at=now,
                    updated_at=now,
                )
                db.add(record)
            else:
                record.parent_task_id = parent_task_id
                record.children_ids = children_ids
                record.evaluator_id = evaluator_id
                record.acceptance_criteria = acceptance_criteria
                record.handoff_note = handoff_note
                record.updated_at = now
            db.commit()

    def list_tasks_for_run(self, run_id: str) -> list[dict]:
        with self._sf() as db:
            q = (
                db.query(TaskCenterTaskRecord)
                .filter(TaskCenterTaskRecord.run_id == run_id)
                .order_by(TaskCenterTaskRecord.created_at.asc())
            )
            return [_serialize_task(record) for record in q.all()]

    def list_graph_for_run(self, run_id: str) -> list[dict]:
        with self._sf() as db:
            q = (
                db.query(TaskCenterGraphRecord)
                .filter(TaskCenterGraphRecord.run_id == run_id)
                .order_by(TaskCenterGraphRecord.created_at.asc())
            )
            return [_serialize_graph(record) for record in q.all()]
