"""main_workspace = base repo + LayerStack snapshots.

This package re-exports the public surface from ``sandbox.layer_stack`` and
``sandbox.occ`` for the workspace trichotomy. Implementing modules continue to
live under those packages to preserve existing import paths.
"""

from sandbox.layer_stack import LayerStack, prepare_workspace_snapshot
from sandbox.occ import CommitQueue
from sandbox.occ.changeset import Change, DeleteChange, WriteChange


__all__ = [
    "LayerStack",
    "prepare_workspace_snapshot",
    "CommitQueue",
    "Change",
    "WriteChange",
    "DeleteChange",
]
