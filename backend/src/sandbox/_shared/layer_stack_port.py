"""Single canonical layer-stack Protocol for workspace pipelines.

Replaces three near-identical predecessors that were deleted in Phase 2.6 C3.5b:

* ``sandbox.ephemeral_workspace._types.OverlayLayerStackClient``
* ``sandbox.isolated_workspace._types.LayerStackPort``
* ``sandbox._shared.shell_contract.WorkspaceLeaseClient``

All three had ~80% surface overlap; their divergence was bootstrap-shape
(per-call ``layer_stack_root`` vs bound LayerStackClient). The iws pipeline
now binds a :class:`sandbox.occ.layer_stack_client.LayerStackClient` at
construction time, so both eph and iws speak the same kwarg-only contract
defined here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from sandbox._shared.shell_contract import (
    SnapshotManifest,
    WorkspaceSnapshotLease,
)


class LayerStackPort(Protocol):
    """Layer-stack surface a workspace pipeline needs.

    The kwarg-only signature lets concrete implementations (e.g.
    ``LayerStackClient`` wrapping the in-process ``LayerStack``) keep their
    own positional-arg internal call shape without leaking it through this
    Protocol.
    """

    storage_root: Path

    def prepare_workspace_snapshot(
        self,
        *,
        request_id: str,
    ) -> WorkspaceSnapshotLease: ...

    def release_lease(self, *, lease_id: str) -> bool: ...

    def read_active_manifest(self) -> SnapshotManifest: ...


__all__ = ["LayerStackPort"]
