"""Experimental bounded overlay layer stack prototype.

This package is intentionally separate from production backend code. It models
the per-call snapshot layer stack, lease retention, squash, and OCC-style commit
decisions described in ``.omc/plans/per-call-snapshot-layer-stack.md``.
"""

from stack_overlay.layer_manager import LayerManager
from stack_overlay.models import (
    ChangeStatus,
    CommitResult,
    DeleteChange,
    FileResult,
    LayerChange,
    Lease,
    Manifest,
    WriteChange,
)
from stack_overlay.occ import OccCommitter, content_hash
from stack_overlay.policies import (
    DirectMergePolicy,
    LeaseBudget,
    LeaseSnapshot,
    ShellCommitGate,
    ShellMode,
    StalenessPolicy,
    classify_shell_mode,
)

__all__ = [
    "ChangeStatus",
    "CommitResult",
    "DeleteChange",
    "FileResult",
    "LayerChange",
    "LayerManager",
    "Lease",
    "Manifest",
    "OccCommitter",
    "DirectMergePolicy",
    "LeaseBudget",
    "LeaseSnapshot",
    "ShellCommitGate",
    "ShellMode",
    "StalenessPolicy",
    "WriteChange",
    "classify_shell_mode",
    "content_hash",
]
