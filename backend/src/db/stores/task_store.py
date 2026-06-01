"""Request and task persistence store."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from db.models.request import RequestRecord
from db.models.task import TaskRecord
from db.stores.base import SyncStoreMixin


SerializedRow = dict[str, Any]


def _serialize_request(record: RequestRecord) -> SerializedRow:
    return {
        "id": record.id,
        "cwd": record.cwd,
        "sandbox_id": record.sandbox_id,
        "request_prompt": record.request_prompt,
        "root_task_id": record.root_task_id,
        "status": record.status,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
        "finished_at": record.finished_at.isoformat() if record.finished_at else None,
    }


def _serialize_task(record: TaskRecord) -> SerializedRow:
    return {
        "task_id": record.id,
        "request_id": record.request_id,
        "role": record.role,
        "agent_name": record.agent_name,
        "instruction": record.instruction,
        "status": record.status,
        "workflow_id": record.workflow_id,
        "iteration_id": record.iteration_id,
        "attempt_id": record.attempt_id,
        "outcomes": record.outcomes or [],
        "terminal_tool_result": record.terminal_tool_result,
        "needs": record.needs or [],
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
    }


class TaskStore(SyncStoreMixin):
    """CRUD operations for requests and tasks."""

    def create_request(
        self,
        *,
        request_id: str,
        cwd: str,
        sandbox_id: str | None,
        request_prompt: str,
    ) -> SerializedRow:
        with self._sf() as db:
            now = datetime.now(UTC)
            record = RequestRecord(
                id=request_id,
                cwd=cwd,
                sandbox_id=sandbox_id,
                request_prompt=request_prompt,
                status="running",
                created_at=now,
                updated_at=now,
            )
            db.add(record)
            db.commit()
            db.refresh(record)
            return _serialize_request(record)

    def get_request(self, request_id: str) -> SerializedRow | None:
        with self._sf() as db:
            record = db.get(RequestRecord, request_id)
            return _serialize_request(record) if record is not None else None

    def set_root_task_id(self, request_id: str, root_task_id: str) -> SerializedRow:
        with self._sf() as db:
            record = db.get(RequestRecord, request_id)
            if record is None:
                raise LookupError(f"Request {request_id!r} not found")
            record.root_task_id = root_task_id
            record.updated_at = datetime.now(UTC)
            db.commit()
            db.refresh(record)
            return _serialize_request(record)

    def finish_request(self, request_id: str, status: str) -> SerializedRow | None:
        with self._sf() as db:
            record = db.get(RequestRecord, request_id)
            if record is None:
                return None
            if record.status in ("done", "failed"):
                return _serialize_request(record)
            record.status = status
            record.finished_at = datetime.now(UTC)
            record.updated_at = record.finished_at
            db.commit()
            db.refresh(record)
            return _serialize_request(record)

    def upsert_task(
        self,
        *,
        task_id: str,
        request_id: str,
        role: str,
        instruction: str,
        status: str,
        outcomes: list[SerializedRow],
        needs: list[str],
        workflow_id: str | None = None,
        iteration_id: str | None = None,
        attempt_id: str | None = None,
        agent_name: str | None = None,
        terminal_tool_result: dict | None = None,
    ) -> None:
        with self._sf() as db:
            now = datetime.now(UTC)
            record = db.get(TaskRecord, task_id)
            if record is None:
                record = TaskRecord(
                    id=task_id,
                    request_id=request_id,
                    role=role,
                    instruction=instruction,
                    status=status,
                    workflow_id=workflow_id,
                    iteration_id=iteration_id,
                    attempt_id=attempt_id,
                    agent_name=agent_name,
                    outcomes=outcomes,
                    terminal_tool_result=terminal_tool_result,
                    needs=needs,
                    created_at=now,
                    updated_at=now,
                )
                db.add(record)
            else:
                record.request_id = request_id
                record.role = role
                record.instruction = instruction
                record.status = status
                record.workflow_id = workflow_id
                record.iteration_id = iteration_id
                record.attempt_id = attempt_id
                record.agent_name = agent_name
                record.outcomes = outcomes
                record.terminal_tool_result = terminal_tool_result
                record.needs = needs
                record.updated_at = now
            db.commit()

    def get_task(self, task_id: str) -> SerializedRow | None:
        with self._sf() as db:
            record = db.get(TaskRecord, task_id)
            return _serialize_task(record) if record is not None else None

    def list_tasks_for_request(self, request_id: str) -> list[SerializedRow]:
        with self._sf() as db:
            q = (
                db.query(TaskRecord)
                .filter(TaskRecord.request_id == request_id)
                .order_by(TaskRecord.created_at.asc())
            )
            return [_serialize_task(record) for record in q.all()]

    def list_tasks_for_attempt(self, attempt_id: str) -> list[SerializedRow]:
        with self._sf() as db:
            q = (
                db.query(TaskRecord)
                .filter(TaskRecord.attempt_id == attempt_id)
                .order_by(TaskRecord.created_at.asc())
            )
            return [_serialize_task(record) for record in q.all()]

    def set_task_status(
        self,
        task_id: str,
        *,
        status: str,
        outcomes: list[SerializedRow] | None = None,
        terminal_tool_result: dict | None = None,
    ) -> SerializedRow:
        with self._sf() as db:
            record = db.get(TaskRecord, task_id)
            if record is None:
                raise LookupError(f"Task {task_id!r} not found")
            record.status = status
            if outcomes is not None:
                record.outcomes = outcomes
            if terminal_tool_result is not None:
                record.terminal_tool_result = terminal_tool_result
            record.updated_at = datetime.now(UTC)
            db.commit()
            db.refresh(record)
            return _serialize_task(record)

    def set_task_status_if_current(
        self,
        task_id: str,
        *,
        expected_status: str,
        status: str,
        outcomes: list[SerializedRow] | None = None,
        terminal_tool_result: dict | None = None,
    ) -> SerializedRow | None:
        with self._sf() as db:
            record = db.get(TaskRecord, task_id)
            if record is None:
                raise LookupError(f"Task {task_id!r} not found")
            if record.status != expected_status:
                return None
            record.status = status
            if outcomes is not None:
                record.outcomes = outcomes
            if terminal_tool_result is not None:
                record.terminal_tool_result = terminal_tool_result
            record.updated_at = datetime.now(UTC)
            db.commit()
            db.refresh(record)
            return _serialize_task(record)
