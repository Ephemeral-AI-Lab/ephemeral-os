"""Append-only sandbox layer-stack storage primitives."""

from __future__ import annotations

from sandbox.layer_stack.changes import (
    DeleteLayerChange,
    LayerChange,
    OpaqueDirLayerChange,
    SymlinkLayerChange,
    WriteLayerChange,
    aggregate_layer_changes,
    normalize_layer_path,
)
from sandbox.layer_stack.stack import (
    CommitStagingArea,
    LayerStack,
    PrepareWorkspaceSnapshotResult,
)
from sandbox.layer_stack.manifest import (
    LayerRef,
    MANIFEST_SCHEMA_VERSION,
    Manifest,
    ManifestConflictError,
)
from sandbox.layer_stack.transaction import LayerStackTransaction
from sandbox.layer_stack.view import LayerStackStorageError
from sandbox.layer_stack.workspace_binding import (
    WorkspaceBinding,
    WorkspaceBindingError,
    read_workspace_binding,
    require_workspace_binding,
)


def prepare_workspace_snapshot(
    layer_stack: LayerStack,
    owner_request_id: str,
) -> PrepareWorkspaceSnapshotResult:
    """Prepare a namespace-ready snapshot from a LayerStack instance."""
    return layer_stack.prepare_workspace_snapshot(owner_request_id)


__all__ = [
    "CommitStagingArea",
    "DeleteLayerChange",
    "LayerChange",
    "LayerRef",
    "LayerStack",
    "LayerStackStorageError",
    "LayerStackTransaction",
    "MANIFEST_SCHEMA_VERSION",
    "Manifest",
    "ManifestConflictError",
    "OpaqueDirLayerChange",
    "PrepareWorkspaceSnapshotResult",
    "SymlinkLayerChange",
    "WorkspaceBinding",
    "WorkspaceBindingError",
    "WriteLayerChange",
    "aggregate_layer_changes",
    "normalize_layer_path",
    "prepare_workspace_snapshot",
    "read_workspace_binding",
    "require_workspace_binding",
]
