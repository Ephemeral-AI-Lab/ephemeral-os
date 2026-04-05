"""Session persistence store backed by PostgreSQL."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session, sessionmaker

from ephemeralos.db.models.session import SessionRecord
from ephemeralos.engine.messages import ConversationMessage

if TYPE_CHECKING:
    from ephemeralos.server.app_factory import SessionConfig
    from ephemeralos.utils.compact import SessionState

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
        if self._session_factory is None:
            raise RuntimeError("SessionStore not initialised")
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
        full_messages: list[dict] | None = None,
        usage: dict | None = None,
        session_state: dict | None = None,
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
                    full_message_history=full_messages,
                    usage=usage,
                    session_state=session_state,
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
                if full_messages is not None:
                    record.full_message_history = full_messages
                record.usage = usage
                record.session_state = session_state
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

    def load_session_state(
        self,
        config: "SessionConfig",
    ) -> tuple[list[ConversationMessage], "SessionState", list[dict]]:
        """Load conversation history, session context, and full message history.

        Returns (messages, session_state, full_message_history).

        ``messages`` is the operational (possibly compacted) history used by agents.
        ``full_message_history`` is the append-only audit log of all messages.
        If no full history exists yet, it is seeded from ``message_history``
        so existing sessions bootstrap gracefully.
        """
        from ephemeralos.utils.compact import SessionState as _SessionState

        ctx = _SessionState()
        full_history: list[dict] = []

        if self._session_factory is not None:
            record = self.get(config.session_id)
            if record:
                ctx = _SessionState.from_dict(record.session_state)
                # Load full history (or bootstrap from message_history)
                if record.full_message_history:
                    full_history = list(record.full_message_history)
                elif record.message_history:
                    full_history = list(record.message_history)
                if record.message_history:
                    try:
                        msgs = [ConversationMessage.model_validate(m) for m in record.message_history]
                        return msgs, ctx, full_history
                    except Exception:
                        logger.warning("Failed to deserialize messages from DB — starting fresh", exc_info=True)

        # Fallback: initial restore messages (consumed once)
        if config._initial_messages:
            try:
                msgs = [ConversationMessage.model_validate(m) for m in config._initial_messages]
                config._initial_messages = None
                full_history = [m.model_dump(mode="json") for m in msgs]
                return msgs, ctx, full_history
            except Exception:
                logger.warning("Failed to load initial restore messages — starting fresh", exc_info=True)

        return [], ctx, full_history

    def delete(self, session_id: str) -> bool:
        with self._sf() as db:
            record = db.get(SessionRecord, session_id)
            if record is None:
                return False
            db.delete(record)
            db.commit()
            return True
