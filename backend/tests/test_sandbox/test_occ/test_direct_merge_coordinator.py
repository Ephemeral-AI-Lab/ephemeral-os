"""Tests for DirectMergeCoordinator (Step 2 of the OCC gate simplification)."""

from __future__ import annotations

import asyncio

from sandbox.occ.changeset.types import (
    BinaryChange,
    DeleteChange,
    EditChange,
    FileStatus,
    OpaqueDirChange,
    SymlinkChange,
    WriteChange,
)
from sandbox.occ.direct.direct_merge_coordinator import DirectMergeCoordinator
from sandbox.occ.patching.patcher import SearchReplaceEdit


class _RecorderContent:
    """In-memory ContentManager double recording every direct-merge call."""

    def __init__(
        self,
        *,
        files: dict[str, str] | None = None,
        children: dict[str, list[str]] | None = None,
    ) -> None:
        self._files: dict[str, str] = dict(files or {})
        self._children: dict[str, list[str]] = {
            path: list(names) for path, names in (children or {}).items()
        }
        self.calls: list[tuple[str, ...]] = []

    def read(self, path: str, *, allow_missing: bool = False) -> tuple[str, bool]:
        self.calls.append(("read", path))
        if path in self._files:
            return self._files[path], True
        if allow_missing:
            return "", False
        raise FileNotFoundError(path)

    def write(self, path: str, content: str) -> None:
        self.calls.append(("write", path, content))
        self._files[path] = content

    def write_bytes(self, path: str, content: bytes) -> None:
        self.calls.append(("write_bytes", path, repr(content)))
        self._files[path] = content.decode("latin-1")

    def delete(self, path: str) -> None:
        self.calls.append(("delete", path))
        self._files.pop(path, None)

    def delete_path(self, path: str) -> None:
        self.calls.append(("delete_path", path))
        self._files.pop(path, None)
        self._children.pop(path, None)

    def make_symlink(self, path: str, target: str) -> None:
        self.calls.append(("make_symlink", path, target))
        self._files[path] = f"<link:{target}>"

    def list_child_names(self, path: str) -> list[str]:
        self.calls.append(("list_child_names", path))
        return list(self._children.get(path, []))


def test_direct_merge_symlink_dispatches_to_make_symlink() -> None:
    content = _RecorderContent()
    coord = DirectMergeCoordinator(content)
    [result] = asyncio.run(
        coord.apply([SymlinkChange(path="link", target="/abs/target")])
    )
    assert result.status is FileStatus.COMMITTED
    assert ("make_symlink", "link", "/abs/target") in content.calls


def test_direct_merge_binary_write_uses_write_bytes() -> None:
    content = _RecorderContent()
    coord = DirectMergeCoordinator(content)
    [result] = asyncio.run(
        coord.apply([BinaryChange(path="b.dat", final_bytes=b"\x00\xff")])
    )
    assert result.status is FileStatus.COMMITTED
    write_bytes_call = next(c for c in content.calls if c[0] == "write_bytes")
    assert write_bytes_call[1] == "b.dat"


def test_direct_merge_binary_none_deletes_path() -> None:
    content = _RecorderContent(files={"b.dat": "x"})
    coord = DirectMergeCoordinator(content)
    [result] = asyncio.run(
        coord.apply([BinaryChange(path="b.dat", final_bytes=None)])
    )
    assert result.status is FileStatus.COMMITTED
    assert ("delete_path", "b.dat") in content.calls


def test_direct_merge_write_change_writes_text() -> None:
    content = _RecorderContent()
    coord = DirectMergeCoordinator(content)
    change = WriteChange(
        path="a.py",
        base_hash="anyhash",
        base_existed=False,
        final_content="hello",
    )
    [result] = asyncio.run(coord.apply([change]))
    assert result.status is FileStatus.COMMITTED
    assert ("write", "a.py", "hello") in content.calls


def test_direct_merge_delete_change_dispatches_delete_path() -> None:
    content = _RecorderContent(files={"old.py": "bye"})
    coord = DirectMergeCoordinator(content)
    [result] = asyncio.run(
        coord.apply([DeleteChange(path="old.py", base_hash="ignored")])
    )
    assert result.status is FileStatus.COMMITTED
    assert ("delete_path", "old.py") in content.calls


def test_direct_merge_edit_change_best_effort() -> None:
    content = _RecorderContent(files={"a.py": "alpha\nbeta\n"})
    coord = DirectMergeCoordinator(content)
    change = EditChange(
        path="a.py",
        edits=(SearchReplaceEdit(old_text="beta", new_text="BETA"),),
    )
    [result] = asyncio.run(coord.apply([change]))
    assert result.status is FileStatus.COMMITTED
    assert content._files["a.py"] == "alpha\nBETA\n"


def test_direct_merge_edit_change_skips_when_anchor_missing() -> None:
    """Best-effort: missing anchor does not abort, just skips the edit."""
    content = _RecorderContent(files={"a.py": "alpha\n"})
    coord = DirectMergeCoordinator(content)
    change = EditChange(
        path="a.py",
        edits=(SearchReplaceEdit(old_text="missing", new_text="X"),),
    )
    [result] = asyncio.run(coord.apply([change]))
    assert result.status is FileStatus.COMMITTED
    # No write was emitted because nothing changed.
    assert not any(c[0] == "write" for c in content.calls)
    assert content._files["a.py"] == "alpha\n"


def test_direct_merge_opaque_dir_prunes_unkept_children() -> None:
    content = _RecorderContent(children={"dir": ["keep", "drop", "sub"]})
    coord = DirectMergeCoordinator(content)
    change = OpaqueDirChange(path="dir", kept_children=frozenset({"keep", "sub"}))
    [result] = asyncio.run(coord.apply([change]))
    assert result.status is FileStatus.COMMITTED
    deleted = [c for c in content.calls if c[0] == "delete_path"]
    assert ("delete_path", "dir/drop") in deleted
    assert ("delete_path", "dir/keep") not in deleted
    assert ("delete_path", "dir/sub") not in deleted


def test_direct_merge_failure_returns_failed_result() -> None:
    class _BoomContent(_RecorderContent):
        def write(self, path: str, content: str) -> None:  # type: ignore[override]
            raise OSError("disk full")

    content = _BoomContent()
    coord = DirectMergeCoordinator(content)
    change = WriteChange(
        path="a.py",
        base_hash="",
        base_existed=False,
        final_content="hello",
    )
    [result] = asyncio.run(coord.apply([change]))
    assert result.status is FileStatus.FAILED
    assert "disk full" in result.message


def test_direct_merge_empty_returns_empty() -> None:
    coord = DirectMergeCoordinator(_RecorderContent())
    assert asyncio.run(coord.apply([])) == []
