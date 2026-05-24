"""main_workspace = base repo + LayerStack snapshots.

This package exposes the persistent workspace identity for new code while the
implementation remains in ``sandbox.layer_stack`` and ``sandbox.occ``.
"""

from sandbox.occ.changeset import Change, DeleteChange, WriteChange
from sandbox.occ.commit_queue import CommitQueue
from sandbox.layer_stack import LayerStack


__all__ = [
    "LayerStack",
    "CommitQueue",
    "Change",
    "WriteChange",
    "DeleteChange",
]
