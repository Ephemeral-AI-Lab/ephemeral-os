"""Tests for the file memory store and service."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db.models  # noqa: F401  -- registers models with Base.metadata
from code_intelligence.core.types import SymbolInfo, SymbolKind
from db.base import Base
from db.stores.file_memory_note_store import FileMemoryNoteStore
from file_memory.service import FileMemoryService


def _store() -> FileMemoryNoteStore:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    store = FileMemoryNoteStore()
    store.initialize(sf)
    return store


class _FakeCodeIntelligence:
    def __init__(
        self,
        *,
        files: dict[tuple[str, str], str],
        symbols: dict[tuple[str, str], list[SymbolInfo]] | None = None,
    ) -> None:
        self._files = files
        self._symbols = symbols or {}

    def read_file(
        self, *, sandbox_id: str, file_path: str
    ) -> tuple[str, str]:
        content = self._files[(sandbox_id, file_path)]
        return content, "fake-hash"

    def file_symbols(
        self,
        *,
        sandbox_id: str,
        file_path: str,
        content: str,
    ) -> list[SymbolInfo]:
        del content
        return list(self._symbols.get((sandbox_id, file_path), []))


def test_append_and_list_round_trips_fields() -> None:
    store = _store()

    appended = store.append_note(
        sandbox_id="sb1",
        file_path="src/a.py",
        note_type="write",
        note="rewrote main()",
        agent_id="agent-1",
        task_id="task-1",
    )

    notes = store.list_notes(sandbox_id="sb1", file_path="src/a.py")

    assert len(notes) == 1
    note = notes[0]
    assert note.id == appended.id
    assert note.sandbox_id == "sb1"
    assert note.file_path == "src/a.py"
    assert note.note_type == "write"
    assert note.note == "rewrote main()"
    assert note.agent_id == "agent-1"
    assert note.task_id == "task-1"
    assert note.created_at is not None


def test_list_notes_filters_by_sandbox_and_path_in_insertion_order() -> None:
    store = _store()
    store.append_note(
        sandbox_id="sb1", file_path="src/a.py", note_type="write", note="first"
    )
    store.append_note(
        sandbox_id="sb1",
        file_path="src/a.py",
        note_type="exploration",
        note="second",
    )
    store.append_note(
        sandbox_id="sb1", file_path="src/b.py", note_type="write", note="other"
    )
    store.append_note(
        sandbox_id="sb2", file_path="src/a.py", note_type="write", note="other-sandbox"
    )

    notes = store.list_notes(sandbox_id="sb1", file_path="src/a.py")
    assert [n.note for n in notes] == ["first", "second"]


def test_get_file_memory_groups_notes_by_type() -> None:
    store = _store()
    store.append_note(
        sandbox_id="sb1", file_path="src/a.py", note_type="write", note="W1"
    )
    store.append_note(
        sandbox_id="sb1", file_path="src/a.py", note_type="exploration", note="E1"
    )
    store.append_note(
        sandbox_id="sb1", file_path="src/a.py", note_type="write", note="W2"
    )

    code = _FakeCodeIntelligence(files={("sb1", "src/a.py"): ""})
    service = FileMemoryService(note_store=store, code_intelligence=code)

    memory = service.get_file_memory(sandbox_id="sb1", file_path="src/a.py")

    assert memory.write_notes == ["W1", "W2"]
    assert memory.exploration_notes == ["E1"]


def test_get_file_memory_reports_line_count() -> None:
    store = _store()
    code = _FakeCodeIntelligence(
        files={("sb1", "src/a.py"): "line1\nline2\nline3\n"}
    )
    service = FileMemoryService(note_store=store, code_intelligence=code)

    memory = service.get_file_memory(sandbox_id="sb1", file_path="src/a.py")

    assert memory.line_count == 3
    assert memory.write_notes == []
    assert memory.exploration_notes == []
    assert memory.symbols == []


def test_get_file_memory_passes_through_symbols() -> None:
    store = _store()
    sym = SymbolInfo(
        name="main", kind=SymbolKind.FUNCTION, file_path="src/a.py", line=1
    )
    code = _FakeCodeIntelligence(
        files={("sb1", "src/a.py"): "def main():\n    pass\n"},
        symbols={("sb1", "src/a.py"): [sym]},
    )
    service = FileMemoryService(note_store=store, code_intelligence=code)

    memory = service.get_file_memory(sandbox_id="sb1", file_path="src/a.py")

    assert memory.sandbox_id == "sb1"
    assert memory.file_path == "src/a.py"
    assert memory.symbols == [sym]
    assert memory.line_count == 2
