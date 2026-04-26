"""File memory persistence model."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import CheckConstraint, DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class FileMemoryNoteRecord(Base):
    """A single agent-authored note attached to a file path in a sandbox."""

    __tablename__ = "file_memory_notes"
    __table_args__ = (
        CheckConstraint(
            "note_type IN ('write', 'exploration')",
            name="ck_file_memory_notes_note_type",
        ),
        Index(
            "ix_file_memory_notes_file",
            "sandbox_id",
            "file_path",
            "created_at",
        ),
        Index(
            "ix_file_memory_notes_type",
            "sandbox_id",
            "file_path",
            "note_type",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    sandbox_id: Mapped[str] = mapped_column(String(128))
    file_path: Mapped[str] = mapped_column(String(1024))
    note_type: Mapped[str] = mapped_column(String(32))
    note: Mapped[str] = mapped_column(Text)
    agent_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    task_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    def __repr__(self) -> str:
        return (
            f"<FileMemoryNoteRecord id={self.id!r} "
            f"sandbox={self.sandbox_id!r} path={self.file_path!r} "
            f"type={self.note_type!r}>"
        )
