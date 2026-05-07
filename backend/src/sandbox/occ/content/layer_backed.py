"""Layer-stack backed content reads for OCC validation."""

from __future__ import annotations

from sandbox.layer_stack.manifest import Manifest
from sandbox.occ.ports import SnapshotReader


class LayerBackedContent:
    """Read path bytes from a specific layer-stack manifest."""

    def __init__(self, snapshot_reader: SnapshotReader) -> None:
        self._snapshot_reader = snapshot_reader

    def read_bytes(self, path: str, manifest: Manifest) -> tuple[bytes | None, bool]:
        """Return ``(content, exists)`` for *path* in *manifest*."""
        return self._snapshot_reader.read_bytes(path, manifest)


__all__ = ["LayerBackedContent"]
