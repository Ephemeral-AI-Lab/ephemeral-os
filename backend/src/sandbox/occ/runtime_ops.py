"""Small runtime helpers shared by OCC preparation and future commit phases."""

from __future__ import annotations

import hashlib

from sandbox.layer_stack.manifest import Manifest
from sandbox.layer_stack.stack_manager import LayerStackManager


def content_hash_bytes(content: bytes) -> str:
    """Return the layer-stack OCC hash for file bytes."""
    return hashlib.sha256(content).hexdigest()


def infer_manifest_base_hash(
    *,
    layer_stack: LayerStackManager,
    manifest: Manifest,
    path: str,
) -> str | None:
    """Hash *path* content as it existed in a leased manifest."""
    content, exists = layer_stack.read_bytes(path, manifest)
    if not exists or content is None:
        return None
    return content_hash_bytes(content)


__all__ = ["content_hash_bytes", "infer_manifest_base_hash"]
