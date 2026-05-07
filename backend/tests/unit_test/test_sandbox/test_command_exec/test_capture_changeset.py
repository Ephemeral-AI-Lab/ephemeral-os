"""Command-exec capture to OCC conversion tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from sandbox.command_exec.capture.changeset import workspace_changes_to_occ_changes
from sandbox.occ.changeset.types import (
    DeleteChange,
    OpaqueDirChange,
    SymlinkChange,
    WriteChange,
)
from sandbox.overlay.capture.changes import OverlayPathChange, content_hash


def test_workspace_changes_to_occ_changes_converts_all_supported_kinds(
    tmp_path: Path,
) -> None:
    write_path = tmp_path / "new.txt"
    write_path.write_bytes(b"new")
    link_path = tmp_path / "link"
    os.symlink("/target", link_path)

    changes = workspace_changes_to_occ_changes(
        [
            OverlayPathChange(
                path="src/new.txt",
                kind="write",
                content_path=str(write_path),
                final_hash=content_hash(write_path),
            ),
            OverlayPathChange(
                path="src/old.txt",
                kind="delete",
                content_path=None,
                final_hash=None,
            ),
            OverlayPathChange(
                path="link",
                kind="symlink",
                content_path=str(link_path),
                final_hash=content_hash(link_path, symlink=True),
            ),
            OverlayPathChange(
                path="dir",
                kind="opaque_dir",
                content_path=None,
                final_hash=None,
            ),
            OverlayPathChange(
                path="dir/keep.py",
                kind="write",
                content_path=str(write_path),
                final_hash=content_hash(write_path),
            ),
            OverlayPathChange(
                path="dir/nested/child.py",
                kind="write",
                content_path=str(write_path),
                final_hash=content_hash(write_path),
            ),
        ]
    )

    assert isinstance(changes[0], WriteChange)
    assert changes[0].source == "overlay_capture"
    assert changes[0].final_content == b"new"
    assert isinstance(changes[1], DeleteChange)
    assert changes[1].base_hash is None
    assert isinstance(changes[2], SymlinkChange)
    assert changes[2].target == "/target"
    assert isinstance(changes[3], OpaqueDirChange)
    assert changes[3].kept_children == frozenset({"keep.py", "nested"})


def test_workspace_changes_to_occ_changes_rejects_missing_content_path() -> None:
    invalid_change = object.__new__(OverlayPathChange)
    object.__setattr__(invalid_change, "path", "src/new.txt")
    object.__setattr__(invalid_change, "kind", "write")
    object.__setattr__(invalid_change, "content_path", None)
    object.__setattr__(invalid_change, "final_hash", None)

    with pytest.raises(ValueError, match="lacks content path"):
        workspace_changes_to_occ_changes([invalid_change])
