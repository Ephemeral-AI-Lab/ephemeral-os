"""Tests for FileChangeApplier (Step 2 of the OCC gate simplification)."""

from __future__ import annotations

import asyncio

from sandbox.occ.changeset.types import (
    DeleteChange,
    EditChange,
    FileStatus,
    WriteChange,
)
from sandbox.occ.content.hashing import content_hash
from sandbox.occ.gated.file_change_applier import FileChangeApplier
from sandbox.occ.patching.patcher import SearchReplaceEdit


class _MemContent:
    """In-memory ContentManager double for FileChangeApplier tests."""

    def __init__(self, files: dict[str, str | None] | None = None) -> None:
        self._files: dict[str, str] = {
            path: content for path, content in (files or {}).items() if content is not None
        }
        self.write_calls: list[tuple[str, str]] = []
        self.delete_calls: list[str] = []

    def read(self, path: str, *, allow_missing: bool = False) -> tuple[str, bool]:
        if path in self._files:
            return self._files[path], True
        if allow_missing:
            return "", False
        raise FileNotFoundError(path)

    def write(self, path: str, content: str) -> None:
        self._files[path] = content
        self.write_calls.append((path, content))

    def delete(self, path: str) -> None:
        self._files.pop(path, None)
        self.delete_calls.append(path)


# ---------------------------------------------------------------- write


def test_write_modify_succeeds_when_base_hash_matches() -> None:
    content = _MemContent({"a.py": "old"})
    applier = FileChangeApplier("a.py", content)
    change = WriteChange(
        path="a.py",
        base_hash=content_hash("old"),
        base_existed=True,
        final_content="new",
    )
    [result] = asyncio.run(applier.apply_many([change]))
    assert result.status is FileStatus.COMMITTED
    assert content._files["a.py"] == "new"


def test_write_aborts_when_existence_changed() -> None:
    content = _MemContent({"a.py": "old"})
    applier = FileChangeApplier("a.py", content)
    change = WriteChange(
        path="a.py",
        base_hash="",
        base_existed=False,  # caller asked to create-only
        final_content="new",
    )
    [result] = asyncio.run(applier.apply_many([change]))
    assert result.status is FileStatus.ABORTED_VERSION
    assert "existence changed" in result.message


def test_write_aborts_when_content_changed() -> None:
    content = _MemContent({"a.py": "actual"})
    applier = FileChangeApplier("a.py", content)
    change = WriteChange(
        path="a.py",
        base_hash=content_hash("expected"),
        base_existed=True,
        final_content="new",
    )
    [result] = asyncio.run(applier.apply_many([change]))
    assert result.status is FileStatus.ABORTED_VERSION
    assert content._files["a.py"] == "actual"  # unchanged


def test_write_creates_when_base_existed_false_and_path_absent() -> None:
    content = _MemContent({})
    applier = FileChangeApplier("new.py", content)
    change = WriteChange(
        path="new.py",
        base_hash="",
        base_existed=False,
        final_content="hello",
    )
    [result] = asyncio.run(applier.apply_many([change]))
    assert result.status is FileStatus.COMMITTED
    assert content._files["new.py"] == "hello"


# ---------------------------------------------------------------- edit


def test_edit_strict_unique_anchor_succeeds() -> None:
    content = _MemContent({"a.py": "alpha\nbeta\ngamma\n"})
    applier = FileChangeApplier("a.py", content)
    change = EditChange(
        path="a.py",
        edits=(SearchReplaceEdit(old_text="beta", new_text="BETA"),),
    )
    [result] = asyncio.run(applier.apply_many([change]))
    assert result.status is FileStatus.COMMITTED
    assert content._files["a.py"] == "alpha\nBETA\ngamma\n"


def test_edit_aborts_when_anchor_missing() -> None:
    content = _MemContent({"a.py": "alpha\n"})
    applier = FileChangeApplier("a.py", content)
    change = EditChange(
        path="a.py",
        edits=(SearchReplaceEdit(old_text="missing", new_text="X"),),
    )
    [result] = asyncio.run(applier.apply_many([change]))
    assert result.status is FileStatus.ABORTED_OVERLAP
    assert "anchor not found" in result.message
    assert content._files["a.py"] == "alpha\n"  # unchanged


def test_edit_aborts_when_anchor_ambiguous() -> None:
    content = _MemContent({"a.py": "x\nx\n"})
    applier = FileChangeApplier("a.py", content)
    change = EditChange(
        path="a.py",
        edits=(SearchReplaceEdit(old_text="x", new_text="Y"),),
    )
    [result] = asyncio.run(applier.apply_many([change]))
    assert result.status is FileStatus.ABORTED_OVERLAP
    assert "ambiguous" in result.message


def test_edit_aborts_when_file_does_not_exist() -> None:
    content = _MemContent({})
    applier = FileChangeApplier("a.py", content)
    change = EditChange(
        path="a.py",
        edits=(SearchReplaceEdit(old_text="x", new_text="y"),),
    )
    [result] = asyncio.run(applier.apply_many([change]))
    assert result.status is FileStatus.ABORTED_OVERLAP
    assert "does not exist" in result.message


def test_edit_with_two_non_overlapping_anchors_both_apply() -> None:
    content = _MemContent({"a.py": "alpha\nbeta\n"})
    applier = FileChangeApplier("a.py", content)
    change = EditChange(
        path="a.py",
        edits=(
            SearchReplaceEdit(old_text="alpha", new_text="ALPHA"),
            SearchReplaceEdit(old_text="beta", new_text="BETA"),
        ),
    )
    [result] = asyncio.run(applier.apply_many([change]))
    assert result.status is FileStatus.COMMITTED
    assert content._files["a.py"] == "ALPHA\nBETA\n"


def test_two_sequential_non_overlapping_edits_both_succeed() -> None:
    """Plan §Success criteria #4: two non-overlapping edits to the same file
    both succeed when run through one OCCGatedCoordinator. The applier itself
    enforces the same property when it receives a 2-element batch.
    """
    content = _MemContent({"a.py": "alpha\nbeta\n"})
    applier = FileChangeApplier("a.py", content)
    edit1 = EditChange(
        path="a.py",
        edits=(SearchReplaceEdit(old_text="alpha", new_text="ALPHA"),),
    )
    edit2 = EditChange(
        path="a.py",
        edits=(SearchReplaceEdit(old_text="beta", new_text="BETA"),),
    )
    results = asyncio.run(applier.apply_many([edit1, edit2]))
    assert [r.status for r in results] == [FileStatus.COMMITTED, FileStatus.COMMITTED]
    assert content._files["a.py"] == "ALPHA\nBETA\n"


def test_second_edit_aborts_when_first_makes_anchor_ambiguous() -> None:
    """Plan §Success criteria #5: if the first edit's commit makes the second
    edit's anchor match >=2 times, the second aborts ABORTED_OVERLAP.
    """
    content = _MemContent({"a.py": "x\n"})
    applier = FileChangeApplier("a.py", content)
    # First edit doubles the file so "x" matches twice afterwards.
    first = EditChange(
        path="a.py",
        edits=(SearchReplaceEdit(old_text="x\n", new_text="x\nx\n"),),
    )
    # Second edit's anchor is now ambiguous.
    second = EditChange(
        path="a.py",
        edits=(SearchReplaceEdit(old_text="x", new_text="Y"),),
    )
    results = asyncio.run(applier.apply_many([first, second]))
    assert results[0].status is FileStatus.COMMITTED
    assert results[1].status is FileStatus.ABORTED_OVERLAP


# ---------------------------------------------------------------- delete


def test_delete_succeeds_when_hash_matches() -> None:
    content = _MemContent({"a.py": "bye"})
    applier = FileChangeApplier("a.py", content)
    change = DeleteChange(path="a.py", base_hash=content_hash("bye"))
    [result] = asyncio.run(applier.apply_many([change]))
    assert result.status is FileStatus.COMMITTED
    assert "a.py" not in content._files


def test_delete_aborts_when_hash_mismatched() -> None:
    content = _MemContent({"a.py": "actual"})
    applier = FileChangeApplier("a.py", content)
    change = DeleteChange(path="a.py", base_hash=content_hash("different"))
    [result] = asyncio.run(applier.apply_many([change]))
    assert result.status is FileStatus.ABORTED_VERSION
    assert "a.py" in content._files


def test_delete_no_op_success_when_file_already_gone() -> None:
    content = _MemContent({})
    applier = FileChangeApplier("a.py", content)
    change = DeleteChange(path="a.py", base_hash=content_hash("never"))
    [result] = asyncio.run(applier.apply_many([change]))
    assert result.status is FileStatus.COMMITTED
    assert content.delete_calls == []  # we did not call delete on a missing file


# ---------------------------------------------------------------- mixed


def test_mixed_batch_processes_each_change_against_fresh_read() -> None:
    """The applier must re-read between changes so the second sees the first's writes."""
    content = _MemContent({"a.py": "v1"})
    applier = FileChangeApplier("a.py", content)
    write_change = WriteChange(
        path="a.py",
        base_hash=content_hash("v1"),
        base_existed=True,
        final_content="v2",
    )
    edit_change = EditChange(
        path="a.py",
        edits=(SearchReplaceEdit(old_text="v2", new_text="v3"),),
    )
    results = asyncio.run(applier.apply_many([write_change, edit_change]))
    assert [r.status for r in results] == [FileStatus.COMMITTED, FileStatus.COMMITTED]
    assert content._files["a.py"] == "v3"


def test_unsupported_kind_returns_failed() -> None:
    """Defensive guard for the GatedChange union exhaustiveness."""
    content = _MemContent({})
    applier = FileChangeApplier("a.py", content)

    class _Bogus:
        path = "a.py"

    results = asyncio.run(applier.apply_many([_Bogus()]))  # type: ignore[list-item]
    assert results[0].status is FileStatus.FAILED
