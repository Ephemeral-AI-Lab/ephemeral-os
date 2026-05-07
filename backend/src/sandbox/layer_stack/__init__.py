"""Append-only sandbox layer-stack storage primitives."""

from __future__ import annotations

from sandbox.layer_stack.layer.change import LayerChange
from sandbox.layer_stack.manifest import (
    LayerRef,
    Manifest,
    ManifestConflictError,
)
from sandbox.layer_stack.manager import (
    LayerStackManager,
    PrepareWorkspaceSnapshotResult,
)

__all__ = [
    "LayerChange",
    "LayerRef",
    "LayerStackManager",
    "Manifest",
    "ManifestConflictError",
    "PrepareWorkspaceSnapshotResult",
]
