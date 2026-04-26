"""File memory note persistence store."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from db.models.file_memory import FileMemoryNoteRecord
from db.stores.base import SyncStoreMixin
from file_memory.types import FileMemoryNote, FileMemoryNoteType


def _to_note(record: FileMemoryNoteRecord) -> FileMemoryNote:
    return FileMemoryNote(
        id=record.id,
        sandbox_id=record.sandbox_id,
        file_path=record.file_path,
        note_type=record.note_type,  # type: ignore[arg-type]
        note=record.note,
        created_at=record.created_at,
        agent_id=record.agent_id,
        task_id=record.task_id,
    )


class FileMemoryNoteStore(SyncStoreMixin):
    """CRUD operations for file memory notes."""

    def append_note(
        self,
        *,
        sandbox_id: str,
        file_path: str,
        note_type: FileMemoryNoteType,
        note: str,
        agent_id: str | None = None,
        task_id: str | None = None,
    ) -> FileMemoryNote:
        with self._sf() as db:
            record = FileMemoryNoteRecord(
                id=str(uuid.uuid4()),
                sandbox_id=sandbox_id,
                file_path=file_path,
                note_type=note_type,
                note=note,
                agent_id=agent_id,
                task_id=task_id,
                created_at=datetime.now(UTC),
            )
            db.add(record)
            db.commit()
            db.refresh(record)
            return _to_note(record)

    def list_notes(
        self,
        *,
        sandbox_id: str,
        file_path: str,
    ) -> list[FileMemoryNote]:
        with self._sf() as db:
            q = (
                db.query(FileMemoryNoteRecord)
                .filter(
                    FileMemoryNoteRecord.sandbox_id == sandbox_id,
                    FileMemoryNoteRecord.file_path == file_path,
                )
                .order_by(FileMemoryNoteRecord.created_at.asc())
            )
            return [_to_note(record) for record in q.all()]
