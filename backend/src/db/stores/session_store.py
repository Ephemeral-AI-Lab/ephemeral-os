"""Session persistence store backed by PostgreSQL."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session, sessionmaker

from ephemeralos.db.models.session import SessionRecord

logger = logging.getLogger(__name__)


class SessionStore:
    """CRUD operations for session records."""

    def __init__(self) -> None:
        self._session_factory: sessionmaker[Session] | None = None

    def initialize(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        logger.info("SessionStore initialised")

    @property
    def _sf(self) -> sessionmaker[Session]:
        assert self._session_factory is not None, "SessionStore not initialised"
        return self._session_factory

    # -- writes ----------------------------------------------------------------

    def upsert(
        self,
        *,
        session_id: str,
        cwd: str,
        model: str,
        system_prompt: str | None = None,
        messages: list[dict] | None = None,
        usage: dict | None = None,
        summary: str | None = None,
        message_count: int = 0,
    ) -> SessionRecord:
        """Insert or update a session record."""
        with self._sf() as db:
            record = db.get(SessionRecord, session_id)
            now = datetime.now(timezone.utc)
            if record is None:
                record = SessionRecord(
                    id=session_id,
                    cwd=cwd,
                    model=model,
                    system_prompt=system_prompt,
                    message_history=messages,
                    usage=usage,
                    summary=summary,
                    message_count=message_count,
                    created_at=now,
                    updated_at=now,
                )
                db.add(record)
            else:
                record.model = model
                record.system_prompt = system_prompt
                record.message_history = messages
                record.usage = usage
                record.summary = summary
                record.message_count = message_count
                record.updated_at = now
            db.commit()
            db.refresh(record)
            return record

    # -- reads -----------------------------------------------------------------

    def get(self, session_id: str) -> SessionRecord | None:
        with self._sf() as db:
            return db.get(SessionRecord, session_id)

    def list_sessions(self, cwd: str | None = None, limit: int = 20) -> list[dict]:
        """List sessions, optionally filtered by cwd, newest first."""
        with self._sf() as db:
            q = db.query(SessionRecord)
            if cwd:
                q = q.filter(SessionRecord.cwd == cwd)
            q = q.order_by(SessionRecord.created_at.desc()).limit(limit)
            return [
                {
                    "session_id": r.id,
                    "summary": r.summary or "",
                    "message_count": r.message_count,
                    "model": r.model,
                    "created_at": r.created_at.timestamp() if r.created_at else 0,
                }
                for r in q.all()
            ]

    def delete(self, session_id: str) -> bool:
        with self._sf() as db:
            record = db.get(SessionRecord, session_id)
            if record is None:
                return False
            db.delete(record)
            db.commit()
            return True
