"""Mode-agnostic protocol types shared by sandbox workspace pipelines."""

from sandbox._shared.layer_stack_port import LayerStackPort
from sandbox._shared.shell_contract import (
    ChangesetResultLike,
    OCCMutationClient,
    SnapshotManifest,
    WorkspaceCapturePublishResult,
    WorkspaceSnapshotLease,
)

__all__ = [
    "ChangesetResultLike",
    "LayerStackPort",
    "OCCMutationClient",
    "SnapshotManifest",
    "WorkspaceCapturePublishResult",
    "WorkspaceSnapshotLease",
]
