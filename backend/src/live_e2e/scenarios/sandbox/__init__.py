"""Sandbox subsystem scenarios — OCC, overlay, layerstack, LSP, daemon.

Drive the sandbox subsystem through tool calls; assert on
``EventType.SANDBOX_*`` events emitted from tool completions and on file
content read back through the sandbox toolkit.

Implemented (reference scenarios):
- :class:`OccConcurrentConflicts`
"""

from __future__ import annotations

from live_e2e.scenarios.sandbox.occ_concurrent_conflicts import (
    OccConcurrentConflicts,
)

__all__ = ["OccConcurrentConflicts"]
