"""Per-process counters for audited overlay shell operations."""

from __future__ import annotations

import threading
from dataclasses import dataclass


@dataclass
class OverlayCounters:
    """Aggregate counters surfaced to overlay callers and tests."""

    snap_build_ms: int = 0
    mount_setup_ms: int = 0
    cmd_ms: int = 0
    diff_ms: int = 0
    merge_back_ms: int = 0
    upper_bytes: int = 0
    upper_files: int = 0
    gitinclude_changes: int = 0
    gitignore_changes: int = 0
    direct_merged_bytes: int = 0
    whiteouts_gitinclude: int = 0
    whiteouts_gitignore_refused: int = 0
    dotgit_rejects: int = 0
    upper_full_failures: int = 0
    gitignore_changes_after_aborted_gitinclude: int = 0
    mixed_gitinclude_gitignore_ops: int = 0
    mixed_partial_apply_ops: int = 0
    ops_total: int = 0
    ops_rejected: int = 0


_OVERLAY_COUNTERS = OverlayCounters()
_OVERLAY_LOCK = threading.Lock()


def overlay_counters_snapshot() -> OverlayCounters:
    """Return a consistent copy of the overlay counter state."""
    with _OVERLAY_LOCK:
        return OverlayCounters(**_OVERLAY_COUNTERS.__dict__)


def record_overlay_op(**fields: int) -> None:
    """Additively record one overlay operation.

    Unknown keys are ignored so overlay metadata can evolve without breaking
    older callers.
    """
    with _OVERLAY_LOCK:
        for key, value in fields.items():
            if hasattr(_OVERLAY_COUNTERS, key):
                setattr(_OVERLAY_COUNTERS, key, getattr(_OVERLAY_COUNTERS, key) + int(value))
