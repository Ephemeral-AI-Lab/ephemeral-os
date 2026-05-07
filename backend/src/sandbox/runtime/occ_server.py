"""occ-server logical module — OCC mutation gate composition.

Phase 05 establishes occ-server as the internal mutation gate consumed
through :class:`OCCClient.apply_changeset`. The host-callable surface is
defined by :data:`sandbox.runtime.occ_handlers.OCC_OP_TABLE`, which is
re-exported here for symmetry with the simplified plan's server topology.

This module owns no path classification: in-workspace classification
lives on command-exec (:mod:`sandbox.runtime.handlers._common`) per
§1, single source of truth.
"""

from __future__ import annotations

from sandbox.runtime.occ_handlers import OCC_OP_TABLE


__all__ = ["OCC_OP_TABLE"]
