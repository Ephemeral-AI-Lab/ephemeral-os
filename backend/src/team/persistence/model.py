"""Team definition persistence model."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, String, Text
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TeamDefinitionRecord(Base):
    """Role-based team composition stored in the database.

    ``entry_planner`` is the agent that receives the user request first.
    ``roster`` maps role names to lists of agent-definition names.
    Broken references are caught at ``TeamRun`` start time.
    """

    __tablename__ = "team_definitions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    entry_planner: Mapped[str] = mapped_column(String(128))
    roster: Mapped[dict[str, list[str]]] = mapped_column(JSON, default=dict)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:
        return f"<TeamDefinitionRecord name={self.name!r} entry_planner={self.entry_planner!r}>"
