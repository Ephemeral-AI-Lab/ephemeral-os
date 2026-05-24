"""Mode-agnostic protocol types shared by sandbox workspace pipelines."""

from sandbox.ephemeral_workspace.shell_contract import (
    ChangesetResultLike,
    OCCMutationClient,
    SnapshotManifest,
    WorkspaceCapturePublishResult,
    WorkspaceLeaseClient,
    WorkspaceSnapshotLease,
)

__all__ = [
    "ChangesetResultLike",
    "OCCMutationClient",
    "SnapshotManifest",
    "WorkspaceCapturePublishResult",
    "WorkspaceLeaseClient",
    "WorkspaceSnapshotLease",
]
