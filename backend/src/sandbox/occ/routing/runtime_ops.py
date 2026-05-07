"""Runtime helpers for OCC hash/base-hash derivation."""

from __future__ import annotations

from sandbox.layer_stack.manifest import Manifest
from sandbox.occ.content.hashing import ContentHasher
from sandbox.occ.ports import SnapshotReader


def content_hash_bytes(content: bytes) -> str:
    """Return the layer-stack OCC hash for file bytes."""
    return ContentHasher().hash_bytes(content)


def infer_manifest_base_hash(
    *,
    snapshot_reader: SnapshotReader,
    manifest: Manifest,
    path: str,
) -> str | None:
    """Hash *path* content as it existed in a leased manifest."""
    content, exists = snapshot_reader.read_bytes(path, manifest)
    if not exists or content is None:
        return None
    return content_hash_bytes(content)


__all__ = [
    "content_hash_bytes",
    "infer_manifest_base_hash",
]
