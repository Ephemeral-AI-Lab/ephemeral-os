"""Last-writer-wins coordinator for direct (un-gated) changes.

Used for symlinks, opaque-dir prunes, binary content, and any change whose
target path is gitignored or external (outside the workspace). No
synchronization, no conflict detection.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from sandbox.occ.changeset.types import (
    BinaryChange,
    Change,
    DeleteChange,
    EditChange,
    FileResult,
    FileStatus,
    OpaqueDirChange,
    SymlinkChange,
    WriteChange,
)
from sandbox.occ.content.manager import ContentManager
from sandbox.occ.patching.patcher import SearchReplaceEngine


class DirectMergeCoordinator:
    """Apply direct changes without locking or conflict detection."""

    def __init__(self, content: ContentManager) -> None:
        self._content = content
        self._engine = SearchReplaceEngine()

    async def apply(self, changes: Sequence[Change]) -> list[FileResult]:
        if not changes:
            return []
        return await asyncio.gather(*(self._apply_one(change) for change in changes))

    async def _apply_one(self, change: Change) -> FileResult:
        try:
            if isinstance(change, SymlinkChange):
                await asyncio.to_thread(
                    self._content.make_symlink, change.path, change.target
                )
                return FileResult(path=change.path, status=FileStatus.COMMITTED)
            if isinstance(change, OpaqueDirChange):
                await asyncio.to_thread(self._prune_opaque_dir, change)
                return FileResult(path=change.path, status=FileStatus.COMMITTED)
            if isinstance(change, BinaryChange):
                if change.final_bytes is None:
                    await asyncio.to_thread(self._content.delete_path, change.path)
                else:
                    await asyncio.to_thread(
                        self._content.write_bytes, change.path, change.final_bytes
                    )
                return FileResult(path=change.path, status=FileStatus.COMMITTED)
            if isinstance(change, WriteChange):
                await asyncio.to_thread(
                    self._content.write, change.path, change.final_content
                )
                return FileResult(path=change.path, status=FileStatus.COMMITTED)
            if isinstance(change, EditChange):
                await asyncio.to_thread(self._apply_edit, change)
                return FileResult(path=change.path, status=FileStatus.COMMITTED)
            if isinstance(change, DeleteChange):
                await asyncio.to_thread(self._content.delete_path, change.path)
                return FileResult(path=change.path, status=FileStatus.COMMITTED)
            return FileResult(  # pragma: no cover - exhaustive guard
                path=getattr(change, "path", ""),
                status=FileStatus.FAILED,
                message=f"unsupported direct change kind: {type(change).__name__}",
            )
        except Exception as exc:
            return FileResult(
                path=getattr(change, "path", ""),
                status=FileStatus.FAILED,
                message=str(exc),
            )

    # -- Internals ---------------------------------------------------------

    def _prune_opaque_dir(self, change: OpaqueDirChange) -> None:
        for child in self._content.list_child_names(change.path):
            if child not in change.kept_children:
                self._content.delete_path(f"{change.path}/{child}")

    def _apply_edit(self, change: EditChange) -> None:
        current, existed = self._content.read(change.path, allow_missing=True)
        if not existed:
            return
        outcome = self._engine.apply_many(current, list(change.edits))
        # Best-effort: write whatever the engine could produce, even if some
        # anchors did not match. Direct path is last-writer-wins.
        if outcome.content != current:
            self._content.write(change.path, outcome.content)


__all__ = ["DirectMergeCoordinator"]
