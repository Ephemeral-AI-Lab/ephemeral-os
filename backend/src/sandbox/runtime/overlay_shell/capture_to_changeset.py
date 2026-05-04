"""Convert Phase 02 overlay upperdir capture into OCC typed changes."""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

from sandbox.occ.changeset.builders import (
    build_shell_delete_change,
    build_shell_write_change,
)
from sandbox.occ.changeset.types import Change, OpaqueDirChange, SymlinkChange
from sandbox.overlay.capture.changes import UpperChange


def capture_to_changeset(upper_changes: Sequence[UpperChange]) -> tuple[Change, ...]:
    """Convert raw upperdir changes into source-tagged OCC changes."""
    changes: list[Change] = []
    for upper in upper_changes:
        if upper.kind == "write":
            if upper.content_path is None:
                raise ValueError(f"write upper change lacks content path: {upper.path}")
            changes.append(
                build_shell_write_change(
                    path=upper.path,
                    final_content=Path(upper.content_path).read_bytes(),
                )
            )
            continue
        if upper.kind == "delete":
            changes.append(build_shell_delete_change(path=upper.path))
            continue
        if upper.kind == "symlink":
            if upper.content_path is None:
                raise ValueError(f"symlink upper change lacks content path: {upper.path}")
            changes.append(
                SymlinkChange(
                    path=upper.path,
                    target=os.readlink(upper.content_path),
                    source="shell_capture",
                )
            )
            continue
        if upper.kind == "opaque_dir":
            changes.append(
                OpaqueDirChange(
                    path=upper.path,
                    kept_children=frozenset(_kept_children_for(upper.path, upper_changes)),
                    source="shell_capture",
                )
            )
            continue
    return tuple(changes)


def _kept_children_for(rel: str, all_changes: Sequence[UpperChange]) -> set[str]:
    prefix = f"{rel}/" if rel else ""
    kept: set[str] = set()
    for item in all_changes:
        if item.path == rel or not item.path.startswith(prefix):
            continue
        rest = item.path[len(prefix) :]
        if rest:
            kept.add(rest.split("/", 1)[0])
    return kept


__all__ = ["capture_to_changeset"]
