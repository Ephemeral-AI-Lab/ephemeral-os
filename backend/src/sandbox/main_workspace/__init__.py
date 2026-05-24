"""main_workspace = base repo + LayerStack snapshots.

This package re-exports the public surface from ``sandbox.layer_stack`` and
``sandbox.occ`` for the workspace trichotomy. Implementing modules continue to
live under those packages to preserve existing import paths.
"""

from sandbox.layer_stack import LayerStack
from sandbox.layer_stack.stack import PrepareWorkspaceSnapshotResult
from sandbox.occ import CommitQueue
from sandbox.occ.changeset import Change, DeleteChange, WriteChange


def prepare_workspace_snapshot(
    layer_stack: LayerStack,
    owner_request_id: str,
) -> PrepareWorkspaceSnapshotResult:
    """Prepare a namespace-ready snapshot from a LayerStack instance."""
    return layer_stack.prepare_workspace_snapshot(owner_request_id)

__all__ = [
    "LayerStack",
    "prepare_workspace_snapshot",
    "CommitQueue",
    "Change",
    "WriteChange",
    "DeleteChange",
]
