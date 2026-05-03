"""Tests for the OCC changeset builders (Step 1 of the gate simplification)."""

from __future__ import annotations

from dataclasses import dataclass

from sandbox.occ.changeset.builders import overlay_changes_to_changeset
from sandbox.occ.changeset.types import (
    BinaryChange,
    DeleteChange,
    OpaqueDirChange,
    SymlinkChange,
    WriteChange,
)
from sandbox.occ.content.hashing import content_hash


@dataclass
class _UpperChange:
    rel: str
    kind: str
    base_bytes: bytes | None
    upper_bytes: bytes | None
    base_existed: bool


# ---------------------------------------------------------------- overlay


def test_overlay_regular_utf8_becomes_write_change() -> None:
    upper = [
        _UpperChange(
            rel="src/a.py",
            kind="regular",
            base_bytes=b"old",
            upper_bytes=b"new",
            base_existed=True,
        )
    ]
    [change] = overlay_changes_to_changeset(upper)
    assert isinstance(change, WriteChange)
    assert change.path == "src/a.py"
    assert change.final_content == "new"
    assert change.base_hash == content_hash("old")
    assert change.base_existed is True


def test_overlay_regular_create_has_empty_base_hash() -> None:
    upper = [
        _UpperChange(
            rel="src/new.py",
            kind="regular",
            base_bytes=None,
            upper_bytes=b"hello",
            base_existed=False,
        )
    ]
    [change] = overlay_changes_to_changeset(upper)
    assert isinstance(change, WriteChange)
    assert change.base_existed is False
    assert change.base_hash == ""
    assert change.final_content == "hello"


def test_overlay_regular_non_utf8_becomes_binary_change() -> None:
    upper = [
        _UpperChange(
            rel="bin/data.dat",
            kind="regular",
            base_bytes=None,
            upper_bytes=b"\xff\xfe\x00\x01",
            base_existed=False,
        )
    ]
    [change] = overlay_changes_to_changeset(upper)
    assert isinstance(change, BinaryChange)
    assert change.path == "bin/data.dat"
    assert change.final_bytes == b"\xff\xfe\x00\x01"


def test_overlay_whiteout_existed_becomes_delete_change() -> None:
    upper = [
        _UpperChange(
            rel="src/gone.py",
            kind="whiteout",
            base_bytes=b"content",
            upper_bytes=None,
            base_existed=True,
        )
    ]
    [change] = overlay_changes_to_changeset(upper)
    assert isinstance(change, DeleteChange)
    assert change.path == "src/gone.py"
    assert change.base_hash == content_hash("content")


def test_overlay_whiteout_not_existed_skipped() -> None:
    upper = [
        _UpperChange(
            rel="src/never.py",
            kind="whiteout",
            base_bytes=None,
            upper_bytes=None,
            base_existed=False,
        )
    ]
    assert overlay_changes_to_changeset(upper) == []


def test_overlay_whiteout_non_utf8_base_becomes_binary_delete() -> None:
    upper = [
        _UpperChange(
            rel="bin/old.dat",
            kind="whiteout",
            base_bytes=b"\xff\xfe",
            upper_bytes=None,
            base_existed=True,
        )
    ]
    [change] = overlay_changes_to_changeset(upper)
    assert isinstance(change, BinaryChange)
    assert change.final_bytes is None


def test_overlay_symlink_becomes_symlink_change() -> None:
    upper = [
        _UpperChange(
            rel="link",
            kind="symlink",
            base_bytes=None,
            upper_bytes=b"/abs/target",
            base_existed=False,
        )
    ]
    [change] = overlay_changes_to_changeset(upper)
    assert isinstance(change, SymlinkChange)
    assert change.path == "link"
    assert change.target == "/abs/target"


def test_overlay_opaque_dir_records_first_segment_kept_children() -> None:
    upper = [
        _UpperChange(
            rel="dir",
            kind="opaque_dir",
            base_bytes=None,
            upper_bytes=None,
            base_existed=False,
        ),
        _UpperChange(
            rel="dir/keep.py",
            kind="regular",
            base_bytes=None,
            upper_bytes=b"x",
            base_existed=False,
        ),
        _UpperChange(
            rel="dir/sub/inner.py",
            kind="regular",
            base_bytes=None,
            upper_bytes=b"y",
            base_existed=False,
        ),
        _UpperChange(
            rel="other/unrelated.py",
            kind="regular",
            base_bytes=None,
            upper_bytes=b"z",
            base_existed=False,
        ),
    ]
    out = overlay_changes_to_changeset(upper)
    opaque = next(c for c in out if isinstance(c, OpaqueDirChange))
    assert opaque.path == "dir"
    assert opaque.kept_children == frozenset({"keep.py", "sub"})
