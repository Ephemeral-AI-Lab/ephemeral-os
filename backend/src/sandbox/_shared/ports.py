"""Mode-agnostic protocol types shared by sandbox workspace pipelines."""

from sandbox.ephemeral_workspace.shell_contract import (
    ChangesetResultLike,
    EmptyChangesetResult,
    OCCMutationClient,
    SnapshotManifest,
    WorkspaceCapturePublisher,
    WorkspaceCapturePublishResult,
    WorkspaceLeaseClient,
    WorkspaceSnapshotLease,
)

__all__ = [
    "ChangesetResultLike",
    "EmptyChangesetResult",
    "OCCMutationClient",
    "SnapshotManifest",
    "WorkspaceCapturePublisher",
    "WorkspaceCapturePublishResult",
    "WorkspaceLeaseClient",
    "WorkspaceSnapshotLease",
]
