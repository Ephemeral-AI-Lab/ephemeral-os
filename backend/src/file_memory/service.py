"""File memory aggregation service."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from code_intelligence.types import SymbolInfo
from file_memory.types import FileMemory

if TYPE_CHECKING:
    from db.stores.file_memory_note_store import FileMemoryNoteStore


class CodeIntelligenceLike(Protocol):
    """Subset of the code intelligence service the file memory service uses."""

    def read_file(
        self,
        *,
        sandbox_id: str,
        file_path: str,
    ) -> tuple[str, str]:
        """Return ``(content, content_hash)`` for ``file_path`` in the sandbox."""
        ...

    def file_symbols(
        self,
        *,
        sandbox_id: str,
        file_path: str,
        content: str,
    ) -> list[SymbolInfo]:
        """Return symbols defined in ``content`` for ``file_path``."""
        ...


class FileMemoryService:
    """Combines stored notes with live code intelligence into a `FileMemory` view."""

    def __init__(
        self,
        *,
        note_store: "FileMemoryNoteStore",
        code_intelligence: CodeIntelligenceLike,
    ) -> None:
        self.note_store = note_store
        self.code_intelligence = code_intelligence

    def get_file_memory(self, *, sandbox_id: str, file_path: str) -> FileMemory:
        notes = self.note_store.list_notes(
            sandbox_id=sandbox_id,
            file_path=file_path,
        )

        content, _ = self.code_intelligence.read_file(
            sandbox_id=sandbox_id,
            file_path=file_path,
        )

        line_count = len(content.splitlines())

        symbols = self.code_intelligence.file_symbols(
            sandbox_id=sandbox_id,
            file_path=file_path,
            content=content,
        )

        return FileMemory(
            sandbox_id=sandbox_id,
            file_path=file_path,
            line_count=line_count,
            symbols=symbols,
            write_notes=[
                note.note for note in notes if note.note_type == "write"
            ],
            exploration_notes=[
                note.note for note in notes if note.note_type == "exploration"
            ],
        )
