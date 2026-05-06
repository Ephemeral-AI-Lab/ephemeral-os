"""Append-only sandbox layer-stack storage primitives."""

from __future__ import annotations

from sandbox.layer_stack.changes import LayerChange
from sandbox.layer_stack.manifest import (
    LayerRef,
    Manifest,
    ManifestConflictError,
)
from sandbox.layer_stack.stack_manager import (
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
