"""Agent run persistence store."""

from __future__ import annotations

import logging
from datetime import datetime, UTC

from sqlalchemy.orm import Session, sessionmaker

from db.models.agent_run import AgentResponseChunkRecord, AgentRunRecord

logger = logging.getLogger(__name__)


def _serialize_run_summary(r: AgentRunRecord) -> dict:
    """Compact JSON view of an AgentRunRecord for list endpoints."""
    return {
        "id": r.id,
        "parent_run_id": r.parent_run_id,
        "parent_task_id": r.parent_task_id,
        "agent_name": r.agent_name,
        "status": r.status,
        "input_query": r.input_query,
        "event_count": r.event_count,
        "error": r.error,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "cancelled_at": r.cancelled_at.isoformat() if r.cancelled_at else None,
        "cancellation_reason": r.cancellation_reason,
    }


class AgentRunStore:
    """CRUD operations for agent run records and response chunks."""

    def __init__(self) -> None:
        self._session_factory: sessionmaker[Session] | None = None

    def initialize(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        logger.info("AgentRunStore initialised")

    @property
    def is_ready(self) -> bool:
        """True once ``initialize`` has been called with a session factory."""
        return self._session_factory is not None

    @property
    def _sf(self) -> sessionmaker[Session]:
        if self._session_factory is None:
            raise RuntimeError("AgentRunStore not initialised")
        return self._session_factory

    # -- run CRUD --------------------------------------------------------------

    def create_run(
        self,
        *,
        run_id: str,
        session_id: str,
        agent_name: str,
        input_query: str | None = None,
        metadata: dict | None = None,
        parent_run_id: str | None = None,
        parent_task_id: str | None = None,
    ) -> AgentRunRecord:
        """Create a new agent run record.

        ``parent_run_id`` and ``parent_task_id`` are set when this run was
        spawned by another agent (e.g. via run_subagent). Top-level user runs
        leave them ``None``. ``session_id`` is required for the FK; subagent
        runs reuse the parent's ``session_id`` but are filtered out of the
        default ``list_runs()`` query so they do not pollute the parent
        session's transcript.
        """
        with self._sf() as db:
            record = AgentRunRecord(
                id=run_id,
                session_id=session_id,
                parent_run_id=parent_run_id,
                parent_task_id=parent_task_id,
                agent_name=agent_name,
                status="running",
                input_query=input_query,
                metadata_json=metadata,
                started_at=datetime.now(UTC),
            )
            db.add(record)
            db.commit()
            db.refresh(record)
            return record

    def finish_run(
        self,
        run_id: str,
        *,
        status: str = "completed",
        response: dict | None = None,
        message_history: list | None = None,
        compacted_history: list | None = None,
        reasoning: str | None = None,
        error: str | None = None,
        event_count: int = 0,
        cancellation_reason: str | None = None,
    ) -> AgentRunRecord | None:
        with self._sf() as db:
            record = db.get(AgentRunRecord, run_id)
            if record is None:
                return None
            record.status = status
            record.response = response
            record.message_history = message_history
            record.compacted_history = compacted_history
            record.reasoning = reasoning
            record.error = error
            record.event_count = event_count
            now = datetime.now(UTC)
            record.finished_at = now
            if status == "cancelled":
                record.cancelled_at = now
                record.cancellation_reason = cancellation_reason
            db.commit()
            db.refresh(record)
            return record

    def get_run(self, run_id: str) -> AgentRunRecord | None:
        with self._sf() as db:
            return db.get(AgentRunRecord, run_id)

    def list_runs(
        self,
        session_id: str,
        limit: int = 50,
        *,
        include_subagents: bool = False,
    ) -> list[dict]:
        """List runs for a session.

        By default returns only top-level runs (``parent_run_id IS NULL``) so
        the user-facing transcript stays clean. Pass ``include_subagents=True``
        to include subagent runs as well, or use :meth:`list_subagent_runs` to
        fetch the children of a single parent run.
        """
        with self._sf() as db:
            q = db.query(AgentRunRecord).filter(
                AgentRunRecord.session_id == session_id
            )
            if not include_subagents:
                q = q.filter(AgentRunRecord.parent_run_id.is_(None))
            q = q.order_by(AgentRunRecord.created_at.desc()).limit(limit)
            return [_serialize_run_summary(r) for r in q.all()]

    def list_subagent_runs(self, parent_run_id: str, limit: int = 100) -> list[dict]:
        """List all subagent runs spawned by *parent_run_id*, oldest first."""
        with self._sf() as db:
            q = (
                db.query(AgentRunRecord)
                .filter(AgentRunRecord.parent_run_id == parent_run_id)
                .order_by(AgentRunRecord.created_at.asc())
                .limit(limit)
            )
            return [_serialize_run_summary(r) for r in q.all()]

    # -- chunk CRUD ------------------------------------------------------------

    def list_chunks(self, run_id: str, limit: int = 500) -> list[dict]:
        with self._sf() as db:
            q = (
                db.query(AgentResponseChunkRecord)
                .filter(AgentResponseChunkRecord.run_id == run_id)
                .order_by(AgentResponseChunkRecord.seq.asc())
                .limit(limit)
            )
            return [
                {
                    "seq": c.seq,
                    "event_kind": c.event_kind,
                    "content": c.content,
                    "tool_name": c.tool_name,
                    "tool_call_id": c.tool_call_id,
                    "created_at": c.created_at.isoformat() if c.created_at else None,
                }
                for c in q.all()
            ]
