"""ORM model for durable typed team memory records."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, DateTime, Float, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


_JSON_COL = JSON().with_variant(JSONB(), "postgresql")


class TeamMemoryRecordModel(Base):
    """Typed durable memory record for cross-run team history."""

    __tablename__ = "team_memory_records"

    memory_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    project_key: Mapped[str] = mapped_column(String(256), index=True, default="", nullable=False)
    repo_root: Mapped[str] = mapped_column(Text, default="", nullable=False)
    kind: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    scope_json: Mapped[dict[str, Any]] = mapped_column(_JSON_COL, default=dict, nullable=False)
    content_json: Mapped[dict[str, Any]] = mapped_column(_JSON_COL, default=dict, nullable=False)
    source_json: Mapped[dict[str, Any]] = mapped_column(_JSON_COL, default=dict, nullable=False)
    observed_at: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    stale_hint: Mapped[str] = mapped_column(Text, default="", nullable=False)
    superseded_by: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
