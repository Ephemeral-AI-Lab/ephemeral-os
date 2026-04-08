"""SQLAlchemy model for the TeamRun event log.

One row per ``TeamRunEvent``. ``(team_run_id, seq)`` is the primary key,
so in-database ordering matches the JSONL file layout.
"""

from __future__ import annotations

from sqlalchemy import Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from db.base import Base


class TeamRunEventRecord(Base):
    __tablename__ = "team_run_events"

    team_run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(64), index=True)
    ts: Mapped[str] = mapped_column(String(40))
    # JSON portable across Postgres (JSONB) and SQLite (TEXT+JSON funcs).
    data: Mapped[dict] = mapped_column(JSON, default=dict)

    __table_args__ = (
        Index("ix_team_run_events_run_seq", "team_run_id", "seq"),
    )

    def __repr__(self) -> str:  # pragma: no cover — debug only
        return f"<TeamRunEventRecord run={self.team_run_id} seq={self.seq} kind={self.kind}>"
