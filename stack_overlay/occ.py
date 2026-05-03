"""Small OCC-style committer for the experimental layer stack."""

from __future__ import annotations

import hashlib
import threading

from stack_overlay.layer_manager import LayerManager
from stack_overlay.models import (
    ChangeStatus,
    CommitResult,
    DeleteChange,
    FileResult,
    LayerChange,
    Manifest,
    WriteChange,
    normalize_rel_path,
)


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class OccCommitter:
    """Apply gated changes against the current manifest.

    The committer intentionally knows nothing about overlay mounts. It only
    reads the current merged view from ``LayerManager`` and appends accepted
    changes as a new layer.
    """

    def __init__(self, layers: LayerManager) -> None:
        self._layers = layers
        self._lock = threading.Lock()

    def apply(self, changes: list[WriteChange | DeleteChange]) -> CommitResult:
        with self._lock:
            current = self._layers.snapshot()
            accepted: list[LayerChange] = []
            results: list[FileResult] = []
            for change in changes:
                rel = normalize_rel_path(change.path)
                current_text, current_exists = self._layers.read_text(rel, current)
                if isinstance(change, WriteChange):
                    result = self._validate_write(change, current_text, current_exists)
                    if result.status is ChangeStatus.COMMITTED:
                        accepted.append(LayerChange(rel, "write", change.final_content))
                    results.append(result)
                    continue
                result = self._validate_delete(change, current_text, current_exists)
                if result.status is ChangeStatus.COMMITTED and current_exists:
                    accepted.append(LayerChange(rel, "delete"))
                results.append(result)

            next_manifest: Manifest = current
            committed_layer: str | None = None
            if accepted:
                before = self._layers.snapshot()
                next_manifest = self._layers.commit(accepted)
                committed_layer = next(
                    (layer for layer in next_manifest.layers if layer not in before.layers),
                    None,
                )
            return CommitResult(
                manifest=next_manifest,
                files=tuple(results),
                committed_layer=committed_layer,
            )

    def _validate_write(
        self,
        change: WriteChange,
        current_text: str,
        current_exists: bool,
    ) -> FileResult:
        rel = normalize_rel_path(change.path)
        if not change.base_existed:
            if current_exists:
                return FileResult(
                    rel,
                    ChangeStatus.ABORTED_VERSION,
                    "existence changed",
                )
            return FileResult(rel, ChangeStatus.COMMITTED)

        if change.base_hash:
            if not current_exists:
                return FileResult(
                    rel,
                    ChangeStatus.ABORTED_VERSION,
                    "existence changed",
                )
            if content_hash(current_text) != change.base_hash:
                return FileResult(rel, ChangeStatus.ABORTED_VERSION, "content changed")

        return FileResult(rel, ChangeStatus.COMMITTED)

    def _validate_delete(
        self,
        change: DeleteChange,
        current_text: str,
        current_exists: bool,
    ) -> FileResult:
        rel = normalize_rel_path(change.path)
        if not current_exists:
            return FileResult(rel, ChangeStatus.COMMITTED)
        if content_hash(current_text) != change.base_hash:
            return FileResult(
                rel,
                ChangeStatus.ABORTED_VERSION,
                "content changed before delete",
            )
        return FileResult(rel, ChangeStatus.COMMITTED)
