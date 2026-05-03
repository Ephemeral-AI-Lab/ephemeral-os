"""Tests for OCC wire-format helpers (typed Change codec)."""

from __future__ import annotations

import pytest

from sandbox.occ.changeset.types import (
    BinaryChange,
    DeleteChange,
    EditChange,
    OpaqueDirChange,
    SymlinkChange,
    WriteChange,
)
from sandbox.occ.patching.patcher import SearchReplaceEdit
from sandbox.occ.wire import change_from_dict, change_to_dict


def test_edit_change_from_dict_rejects_line_range_edit() -> None:
    with pytest.raises(ValueError, match="unsupported edit kind: line_range"):
        change_from_dict(
            {
                "kind": "edit",
                "path": "/ws/a.py",
                "edits": [
                    {
                        "kind": "line_range",
                        "start_line": 1,
                        "end_line": 2,
                        "new_text": "replacement\n",
                    }
                ],
            }
        )


def test_edit_change_from_dict_rejects_implicit_line_range_edit() -> None:
    with pytest.raises(ValueError, match="unsupported edit kind: line_range"):
        change_from_dict(
            {
                "kind": "edit",
                "path": "/ws/a.py",
                "edits": [
                    {
                        "start_line": 1,
                        "end_line": 2,
                        "new_text": "replacement\n",
                    }
                ],
            }
        )


def test_edit_change_from_dict_requires_search_text() -> None:
    with pytest.raises(ValueError, match="edit requires old_text"):
        change_from_dict(
            {
                "kind": "edit",
                "path": "/ws/a.py",
                "edits": [{"new_text": "replacement\n"}],
            }
        )


def test_edit_change_from_dict_requires_replacement_text() -> None:
    with pytest.raises(ValueError, match="edit requires new_text"):
        change_from_dict(
            {
                "kind": "edit",
                "path": "/ws/a.py",
                "edits": [{"old_text": "target"}],
            }
        )


def test_edit_change_to_dict_rejects_unknown_edit_kind() -> None:
    with pytest.raises(ValueError, match="unsupported edit kind: insert"):
        change_to_dict(
            EditChange(
                path="/ws/a.py",
                edits=({"kind": "insert", "old_text": "a", "new_text": "b"},),  # type: ignore[arg-type]
            )
        )


def test_write_change_round_trip() -> None:
    change = WriteChange(
        path="/ws/a.py",
        base_hash="abc",
        base_existed=True,
        final_content="x = 1\n",
    )
    encoded = change_to_dict(change)
    assert encoded["kind"] == "write"
    decoded = change_from_dict(encoded)
    assert decoded == change


def test_edit_change_round_trip() -> None:
    change = EditChange(
        path="/ws/a.py",
        edits=(SearchReplaceEdit(old_text="old", new_text="new"),),
    )
    encoded = change_to_dict(change)
    assert encoded["kind"] == "edit"
    decoded = change_from_dict(encoded)
    assert decoded == change


def test_delete_change_round_trip() -> None:
    change = DeleteChange(path="/ws/a.py", base_hash="abc")
    decoded = change_from_dict(change_to_dict(change))
    assert decoded == change


def test_symlink_change_round_trip() -> None:
    change = SymlinkChange(path="link", target="/abs/target")
    decoded = change_from_dict(change_to_dict(change))
    assert decoded == change


def test_opaque_dir_change_round_trip() -> None:
    change = OpaqueDirChange(path="dir", kept_children=frozenset({"keep", "sub"}))
    decoded = change_from_dict(change_to_dict(change))
    assert isinstance(decoded, OpaqueDirChange)
    assert decoded.path == "dir"
    assert decoded.kept_children == frozenset({"keep", "sub"})


def test_binary_change_round_trip() -> None:
    change = BinaryChange(path="bin/data.dat", final_bytes=b"\x00\xff\x10")
    decoded = change_from_dict(change_to_dict(change))
    assert decoded == change


def test_binary_change_delete_round_trip() -> None:
    change = BinaryChange(path="bin/data.dat", final_bytes=None)
    decoded = change_from_dict(change_to_dict(change))
    assert decoded == change


def test_unknown_change_kind_raises() -> None:
    with pytest.raises(ValueError, match="unsupported change kind"):
        change_from_dict({"kind": "frob", "path": "/ws/a.py"})
