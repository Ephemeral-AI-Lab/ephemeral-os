"""File memory: per-file agent notes plus code intelligence summaries."""

from file_memory.service import CodeIntelligenceLike, FileMemoryService
from file_memory.types import FileMemory, FileMemoryNote, FileMemoryNoteType

__all__ = [
    "CodeIntelligenceLike",
    "FileMemory",
    "FileMemoryNote",
    "FileMemoryNoteType",
    "FileMemoryService",
]
