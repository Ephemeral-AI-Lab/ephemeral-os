"""Tracked-path OCC merge validation."""

from __future__ import annotations

import time
from collections.abc import Callable

from sandbox.layer_stack.changes import LayerChange, LayerDelta
from sandbox.layer_stack.manifest import Manifest
from sandbox.occ.changeset.prepared import PreparedPathGroup
from sandbox.occ.content.layer_backed_content import LayerBackedContent
from sandbox.occ.changeset.types import (
    DeleteChange,
    EditChange,
    FileResult,
    FileStatus,
    WriteChange,
)
from sandbox.occ.content.hashing import ContentHasher
from sandbox.occ.ports import SnapshotReader

StageWrite = Callable[[str, bytes], LayerChange]


class GatedMerge:
    """Validate gated changes against the active manifest and stage a delta."""

    def __init__(
        self,
        snapshot_reader: SnapshotReader,
        *,
        hasher: ContentHasher | None = None,
    ) -> None:
        self._content = LayerBackedContent(snapshot_reader)
        self._hasher = hasher or ContentHasher()

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
        timings["occ.gated.read_current_s"] = time.perf_counter() - read_start
        initial_exists = current_exists
        content = current_content or b""
        exists = current_exists

        apply_start = time.perf_counter()
        for change in group.changes:
            current_hash = self._hasher.hash_current(content, exists=exists)
            if isinstance(change, WriteChange):
                expected_hash = _base_hash(change.base_hash)
                if current_hash != expected_hash:
                    timings["occ.gated.apply_changes_s"] = (
                        time.perf_counter() - apply_start
                    )
                    return (
                        FileResult(
                            path=group.path,
                            status=FileStatus.ABORTED_VERSION,
                            message="content changed",
                            timings=timings,
                        ),
                        None,
                    )
                content = bytes(change.final_content)
                exists = True
                continue

            if isinstance(change, DeleteChange):
                expected_hash = _base_hash(change.base_hash)
                if current_hash != expected_hash:
                    timings["occ.gated.apply_changes_s"] = (
                        time.perf_counter() - apply_start
                    )
                    return (
                        FileResult(
                            path=group.path,
                            status=FileStatus.ABORTED_VERSION,
                            message="content changed before delete",
                            timings=timings,
                        ),
                        None,
                    )
                content = b""
                exists = False
                continue

            if isinstance(change, EditChange):
                edit_result = _apply_edit_content(
                    group.path,
                    content,
                    exists,
                    change,
                )
                if isinstance(edit_result, FileResult):
                    timings["occ.gated.apply_changes_s"] = (
                        time.perf_counter() - apply_start
                    )
                    return _with_timings(edit_result, timings), None
                content = edit_result
                exists = True
                continue

            timings["occ.gated.apply_changes_s"] = time.perf_counter() - apply_start
            return (
                FileResult(
                    path=group.path,
                    status=FileStatus.REJECTED,
                    message=f"unsupported tracked change kind: {type(change).__name__}",
                    timings=timings,
                ),
                None,
            )

        timings["occ.gated.apply_changes_s"] = time.perf_counter() - apply_start
        stage_start = time.perf_counter()
        delta = _delta_for_final_state(
            path=group.path,
            content=content,
            exists=exists,
            initial_exists=initial_exists,
            stage_write=stage_write,
        )
        timings["occ.gated.stage_delta_s"] = time.perf_counter() - stage_start
        return (
            FileResult(
                path=group.path,
                status=FileStatus.ACCEPTED,
                timings=timings,
            ),
            delta,
        )


def _base_hash(value: str | None) -> str | None:
    return value or None


def _apply_edit_content(
    path: str,
    content: bytes,
    exists: bool,
    change: EditChange,
) -> bytes | FileResult:
    if not exists:
        return FileResult(
            path=path,
            status=FileStatus.ABORTED_OVERLAP,
            message="file does not exist",
        )
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return FileResult(
            path=path,
            status=FileStatus.ABORTED_OVERLAP,
            message="file is not utf-8 text",
        )
    count = text.count(change.old_text)
    if count == 0:
        return FileResult(
            path=path,
            status=FileStatus.ABORTED_OVERLAP,
            message="anchor not found",
        )
    if count != change.expected_occurrences:
        return FileResult(
            path=path,
            status=FileStatus.ABORTED_OVERLAP,
            message="anchor occurrence count mismatch",
        )
    text = text.replace(change.old_text, change.new_text, change.expected_occurrences)
    return text.encode("utf-8")


def _delta_for_final_state(
    *,
    path: str,
    content: bytes,
    exists: bool,
    initial_exists: bool,
    stage_write: StageWrite,
) -> LayerDelta | None:
    if exists:
        return LayerDelta(changes=(stage_write(path, content),))
    if initial_exists:
        return LayerDelta(changes=(LayerChange(path=path, kind="delete"),))
    return None


def _with_timings(result: FileResult, timings: dict[str, float]) -> FileResult:
    return FileResult(
        path=result.path,
        status=result.status,
        message=result.message,
        timings={**result.timings, **timings},
    )


__all__ = ["GatedMerge"]
