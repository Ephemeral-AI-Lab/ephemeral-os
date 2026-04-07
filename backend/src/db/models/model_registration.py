"""Model registration persistence model."""

from __future__ import annotations

from datetime import datetime, timezone, UTC

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class ModelRegistrationRecord(Base):
    """A registered LLM model with its configuration and API credentials."""

    __tablename__ = "model_registrations"

    # NOTE: Using Integer (not BigInteger) for SQLite compatibility.
    # SQLite only supports autoincrement on INTEGER PRIMARY KEY (not BIGINT).
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(256), nullable=False)
    class_path: Mapped[str] = mapped_column(String(512), nullable=False)
    kwargs_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    def __repr__(self) -> str:
        return (
            f"<ModelRegistrationRecord key={self.key!r} "
            f"label={self.label!r} active={self.is_active}>"
        )
