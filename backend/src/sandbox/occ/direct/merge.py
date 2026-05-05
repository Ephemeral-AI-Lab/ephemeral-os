"""Direct-path layer staging for gitignored and untracked changes."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Literal

from sandbox.layer_stack.changes import LayerChange, LayerDelta
from sandbox.layer_stack.manifest import Manifest
from sandbox.layer_stack.stack_manager import LayerStackManager
from sandbox.occ.changeset.prepared import PreparedPathGroup
from sandbox.occ.content.layer_backed_content import LayerBackedContent
from sandbox.occ.changeset.types import (
    DeleteChange,
    EditChange,
    FileResult,
    FileStatus,
    OpaqueDirChange,
    SymlinkChange,
    WriteChange,
)

StageWrite = Callable[[str, bytes], LayerChange]
_FinalKind = Literal["write", "delete", "symlink", "opaque_dir"]


class DirectMerge:
    """Stage direct changes with last-writer-wins semantics."""

    def __init__(self, layer_stack: LayerStackManager) -> None:
        self._content = LayerBackedContent(layer_stack)

    def stage_group(
        self,
        group: PreparedPathGroup,
        *,
        active_manifest: Manifest,
        stage_write: StageWrite,
    ) -> tuple[FileResult, LayerDelta | None]:
        try:
            return self._stage_group(group, active_manifest, stage_write)
        except Exception as exc:
            return (
                FileResult(
                    path=group.path,
                    status=FileStatus.FAILED,
                    message=str(exc),
                ),
                None,
            )

    def _stage_group(
        self,
        group: PreparedPathGroup,
        active_manifest: Manifest,
        stage_write: StageWrite,
    ) -> tuple[FileResult, LayerDelta | None]:
        timings: dict[str, float] = {}
        read_start = time.perf_counter()
        current_content, current_exists = self._content.read_bytes(
            group.path,
            active_manifest,
        )
        timings["occ.direct.read_current_s"] = time.perf_counter() - read_start
        initial_exists = current_exists
        content = current_content or b""
        final_kind: _FinalKind = "write" if current_exists else "delete"
        symlink_target: str | None = None

        apply_start = time.perf_counter()
        for change in group.changes:
            if isinstance(change, OpaqueDirChange):
                content = b""
                final_kind = "opaque_dir"
                symlink_target = None
                continue
            if isinstance(change, SymlinkChange):
                symlink_target = change.target
                content = b""
                final_kind = "symlink"
                continue
            if isinstance(change, WriteChange):
                content = bytes(change.final_content)
                final_kind = "write"
                symlink_target = None
                continue
            if isinstance(change, DeleteChange):
                content = b""
                final_kind = "delete"
                symlink_target = None
                continue
            if isinstance(change, EditChange):
                if final_kind != "write":
                    continue
                try:
                    text = content.decode("utf-8")
                except UnicodeDecodeError:
                    continue
                if change.old_text in text:
                    text = text.replace(change.old_text, change.new_text, 1)
                content = text.encode("utf-8")
                continue

            timings["occ.direct.apply_changes_s"] = time.perf_counter() - apply_start
            return (
                FileResult(
                    path=group.path,
                    status=FileStatus.REJECTED,
                    message=f"unsupported direct change kind: {type(change).__name__}",
                    timings=timings,
                ),
                None,
            )

        timings["occ.direct.apply_changes_s"] = time.perf_counter() - apply_start
        stage_start = time.perf_counter()
        if final_kind == "opaque_dir":
            timings["occ.direct.stage_delta_s"] = time.perf_counter() - stage_start
            return (
                FileResult(
                    path=group.path,
                    status=FileStatus.ACCEPTED,
                    timings=timings,
                ),
                LayerDelta(changes=(LayerChange(path=group.path, kind="opaque_dir"),)),
            )
        if final_kind == "symlink" and symlink_target is not None:
            delta = LayerDelta(
                changes=(
                    LayerChange(
                        path=group.path,
                        kind="symlink",
                        source_path=symlink_target,
                    ),
                )
            )
            timings["occ.direct.stage_delta_s"] = time.perf_counter() - stage_start
            return (
                FileResult(
                    path=group.path,
                    status=FileStatus.ACCEPTED,
                    timings=timings,
                ),
                delta,
            )
        if final_kind == "write":
            delta = LayerDelta(changes=(stage_write(group.path, content),))
            timings["occ.direct.stage_delta_s"] = time.perf_counter() - stage_start
            return (
                FileResult(
                    path=group.path,
                    status=FileStatus.ACCEPTED,
                    timings=timings,
                ),
                delta,
            )
        if final_kind == "delete" and initial_exists:
            timings["occ.direct.stage_delta_s"] = time.perf_counter() - stage_start
            return (
                FileResult(
                    path=group.path,
                    status=FileStatus.ACCEPTED,
                    timings=timings,
                ),
                LayerDelta(changes=(LayerChange(path=group.path, kind="delete"),)),
            )
        timings["occ.direct.stage_delta_s"] = time.perf_counter() - stage_start
        return (
            FileResult(
                path=group.path,
                status=FileStatus.ACCEPTED,
                timings=timings,
            ),
            None,
        )


__all__ = ["DirectMerge"]
