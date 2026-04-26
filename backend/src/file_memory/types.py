"""Data classes for the file memory module."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from code_intelligence.types import SymbolInfo

FileMemoryNoteType = Literal["write", "exploration"]


@dataclass(frozen=True)
class FileMemoryNote:
    id: str
    sandbox_id: str
    file_path: str
    note_type: FileMemoryNoteType
    note: str
    created_at: datetime
    agent_id: str | None = None
    task_id: str | None = None


@dataclass(frozen=True)
class FileMemory:
    sandbox_id: str
    file_path: str
    line_count: int
    symbols: list[SymbolInfo] = field(default_factory=list)
    write_notes: list[str] = field(default_factory=list)
    exploration_notes: list[str] = field(default_factory=list)
