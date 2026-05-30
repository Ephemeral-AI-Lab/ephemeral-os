"""TaskCenter request/run/task persistence store.

Harness-graph persistence has moved to ``db.stores.attempt_store``
and is owned by the new three-axis (request / segment / graph) schema.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from db.models.task_center import (
    TaskCenterRequestRecord,
    TaskCenterRunRecord,
    TaskCenterTaskRecord,
)
from db.stores.base import SyncStoreMixin


SerializedRow = dict[str, Any]


def _serialize_request(record: TaskCenterRequestRecord) -> SerializedRow:
    return {
        "id": record.id,
        "cwd": record.cwd,
        "sandbox_id": record.sandbox_id,
        "request_prompt": record.request_prompt,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
    }


def _serialize_run(record: TaskCenterRunRecord) -> SerializedRow:
    return {
        "id": record.id,
        "request_id": record.request_id,
        "status": record.status,
        "started_at": record.started_at.isoformat() if record.started_at else None,
        "finished_at": record.finished_at.isoformat() if record.finished_at else None,
    }


def _serialize_task(record: TaskCenterTaskRecord) -> SerializedRow:
    return {
        "task_id": record.id,
        "task_center_run_id": record.task_center_run_id,
        "role": record.role,
        "agent_name": record.agent_name,
        "context_message": record.context_message,
        "status": record.status,
        "outcomes": record.outcomes or [],
        "terminal_tool_result": record.terminal_tool_result,
        "needs": record.needs or [],
        "child_workflow_id": record.child_workflow_id,
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
    ) -> SerializedRow:
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
            return _serialize_request(record)

    def get_request(self, request_id: str) -> SerializedRow | None:
        with self._sf() as db:
            record = db.get(TaskCenterRequestRecord, request_id)
            return _serialize_request(record) if record is not None else None

    def create_run(
        self,
        *,
        task_center_run_id: str,
        request_id: str,
    ) -> SerializedRow:
        with self._sf() as db:
            record = TaskCenterRunRecord(
                id=task_center_run_id,
                request_id=request_id,
                status="running",
                started_at=datetime.now(UTC),
            )
            db.add(record)
            db.commit()
            db.refresh(record)
            return _serialize_run(record)

    def finish_run(self, task_center_run_id: str, status: str) -> None:
        with self._sf() as db:
            record = db.get(TaskCenterRunRecord, task_center_run_id)
            if record is None:
                return
            record.status = status
            record.finished_at = datetime.now(UTC)
            db.commit()

    def get_run(self, task_center_run_id: str) -> SerializedRow | None:
        with self._sf() as db:
            record = db.get(TaskCenterRunRecord, task_center_run_id)
            return _serialize_run(record) if record is not None else None

    def upsert_task(
        self,
        *,
        task_id: str,
        task_center_run_id: str,
        role: str,
        context_message: str,
        status: str,
        outcomes: list[SerializedRow],
        needs: list[str],
        agent_name: str | None = None,
        terminal_tool_result: dict | None = None,
        child_workflow_id: str | None = None,
    ) -> None:
        with self._sf() as db:
            now = datetime.now(UTC)
            record = db.get(TaskCenterTaskRecord, task_id)
            if record is None:
                record = TaskCenterTaskRecord(
                    id=task_id,
                    task_center_run_id=task_center_run_id,
                    role=role,
                    agent_name=agent_name,
                    context_message=context_message,
                    status=status,
                    outcomes=outcomes,
                    terminal_tool_result=terminal_tool_result,
                    needs=needs,
                    child_workflow_id=child_workflow_id,
                    created_at=now,
                    updated_at=now,
                )
                db.add(record)
            else:
                record.role = role
                record.agent_name = agent_name
                record.context_message = context_message
                record.status = status
                record.outcomes = outcomes
                record.terminal_tool_result = terminal_tool_result
                record.needs = needs
                record.child_workflow_id = child_workflow_id
                record.updated_at = now
            db.commit()

    def get_task(self, task_id: str) -> SerializedRow | None:
        with self._sf() as db:
            record = db.get(TaskCenterTaskRecord, task_id)
            return _serialize_task(record) if record is not None else None

    def list_tasks_for_run(self, task_center_run_id: str) -> list[SerializedRow]:
        with self._sf() as db:
            q = (
                db.query(TaskCenterTaskRecord)
                .filter(TaskCenterTaskRecord.task_center_run_id == task_center_run_id)
                .order_by(TaskCenterTaskRecord.created_at.asc())
            )
            return [_serialize_task(record) for record in q.all()]

    def list_tasks_for_attempt(
        self, attempt_id: str
    ) -> list[SerializedRow]:
        # Task ids encode the attempt (``<attempt_id>:planner|gen:..|red:..``),
        # so membership is an id-prefix match (the ``task_center_attempt_id``
        # column is gone).
        with self._sf() as db:
            q = (
                db.query(TaskCenterTaskRecord)
                .filter(TaskCenterTaskRecord.id.like(f"{attempt_id}:%"))
                .order_by(TaskCenterTaskRecord.created_at.asc())
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
            record = db.get(TaskCenterTaskRecord, task_id)
            if record is None:
                raise LookupError(f"TaskCenterTask {task_id!r} not found")
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
        child_workflow_id: str | None = None,
    ) -> SerializedRow | None:
        """Compare-and-set task status. Returns the new row, or ``None`` on mismatch.

        The CAS miss is the idempotency primitive for parent-task transitions
        in the child-workflow handoff lifecycle; ``child_workflow_id`` lets the
        ``RUNNING → WAITING_WORKFLOW`` flip and the forward link land in one
        transaction.
        """
        with self._sf() as db:
            record = db.get(TaskCenterTaskRecord, task_id)
            if record is None:
                raise LookupError(f"TaskCenterTask {task_id!r} not found")
            if record.status != expected_status:
                return None
            record.status = status
            if outcomes is not None:
                record.outcomes = outcomes
            if terminal_tool_result is not None:
                record.terminal_tool_result = terminal_tool_result
            if child_workflow_id is not None:
                record.child_workflow_id = child_workflow_id
            record.updated_at = datetime.now(UTC)
            db.commit()
            db.refresh(record)
            return _serialize_task(record)
