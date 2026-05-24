"""Phase 02 upperdir capture tests."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

import sandbox.overlay.capture as upperdir_mod
from sandbox.layer_stack.layer_index import OPAQUE_MARKER, WHITEOUT_PREFIX
from sandbox.occ.overlay_change_conversion import overlay_path_changes_to_occ_changes
from sandbox.overlay.capture import walk_upperdir


def test_upperdir_capture_emits_raw_runtime_changes(tmp_path: Path) -> None:
    upper = tmp_path / "upper"
    upper.mkdir()
    (upper / "app.py").write_text("new\n", encoding="utf-8")
    (upper / f"{WHITEOUT_PREFIX}old.py").write_text("", encoding="utf-8")
    (upper / "pkg").mkdir()
    (upper / "pkg" / OPAQUE_MARKER).write_text("", encoding="utf-8")
    os.symlink("app.py", upper / "current")

    changes = walk_upperdir(upper)

    by_path = {change.path: change for change in changes}
    assert by_path["app.py"].kind == "write"
    assert by_path["app.py"].final_hash == hashlib.sha256(b"new\n").hexdigest()
    assert by_path["old.py"].kind == "delete"
    assert by_path["old.py"].content_path is None
    assert by_path["pkg"].kind == "opaque_dir"
    assert by_path["current"].kind == "symlink"
    assert by_path["current"].final_hash == hashlib.sha256(b"app.py").hexdigest()
    assert not hasattr(by_path["app.py"], "base_bytes")
    assert not hasattr(by_path["app.py"], "gitignore")


def test_opaque_dir_marker_and_xattr_emit_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upper = tmp_path / "upper"
    (upper / "pkg").mkdir(parents=True)
    (upper / "pkg" / OPAQUE_MARKER).write_text("", encoding="utf-8")
    monkeypatch.setattr(
        upperdir_mod,
        "_has_overlay_opaque_xattr",
        lambda entry: entry.name == "pkg",
    )

    changes = walk_upperdir(upper)

    assert [(change.path, change.kind) for change in changes] == [
        ("pkg", "opaque_dir")
    ]


def test_capture_changes_preserves_source_tags(tmp_path: Path) -> None:
    upper = tmp_path / "upper"
    upper.mkdir()
    (upper / "app.py").write_text("new\n", encoding="utf-8")
    (upper / f"{WHITEOUT_PREFIX}old.py").write_text("", encoding="utf-8")
    (upper / "pkg").mkdir()
    (upper / "pkg" / OPAQUE_MARKER).write_text("", encoding="utf-8")
    os.symlink("app.py", upper / "current")

    path_changes = walk_upperdir(upper)

    for source in ("api_write", "overlay_capture"):
        occ_changes = overlay_path_changes_to_occ_changes(
            path_changes,
            source=source,
        )
        assert {change.source for change in occ_changes} == {source}
        assert {change.path for change in occ_changes} == {
            "app.py",
            "old.py",
            "pkg",
            "current",
        }
