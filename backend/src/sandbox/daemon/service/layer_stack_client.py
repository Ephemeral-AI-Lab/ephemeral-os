"""In-process layer-stack client implementing narrow runtime/OCC ports."""

from __future__ import annotations

from contextlib import AbstractContextManager
from pathlib import Path

from sandbox.layer_stack.manifest import Manifest
from sandbox.layer_stack.stack import (
    LayerStack,
    PrepareWorkspaceSnapshotResult,
)
from sandbox.layer_stack.stack import CommitStagingArea
from sandbox.occ.protocols import CommitTransactionPort
from sandbox.daemon.workspace_server import get_layer_stack_manager


class LayerStackClient:
    """Client boundary around the in-process layer-stack manager.

    Forwards OCC port calls to the manager and adapts the per-workspace
    lease API onto the manager's positional signature.
    """

    def __init__(self, layer_stack_root: str | Path | LayerStack) -> None:
        if isinstance(layer_stack_root, (str, Path)):
            self.manager = get_layer_stack_manager(layer_stack_root)
        else:
            self.manager = layer_stack_root

    @property
    def storage_root(self) -> Path:
        return self.manager.storage_root

    def read_active_manifest(self) -> Manifest:
        return self.manager.read_active_manifest()

    def read_bytes(
        self,
        path: str,
        manifest: Manifest,
    ) -> tuple[bytes | None, bool]:
        return self.manager.read_bytes(path, manifest)

    def read_text(
        self,
        path: str,
        manifest: Manifest,
    ) -> tuple[str, bool]:
        return self.manager.read_text(path, manifest)

    def materialize(
        self,
        destination: str | Path,
        manifest: Manifest,
    ) -> None:
        self.manager.materialize(destination, manifest)

    def commit_transaction(self) -> AbstractContextManager[CommitTransactionPort]:
        return self.manager.commit_transaction()

    def allocate_commit_staging(self, request_id: str) -> CommitStagingArea:
        return self.manager.allocate_commit_staging(request_id)

    def drop_commit_staging(self, staging_id: str) -> None:
        self.manager.drop_commit_staging(staging_id)

    def prepare_workspace_snapshot(
        self,
        *,
        request_id: str,
    ) -> PrepareWorkspaceSnapshotResult:
        return self.manager.prepare_workspace_snapshot(request_id)

    def release_lease(self, *, lease_id: str) -> bool:
        return self.manager.release_lease(lease_id)

    def squash(self, *, max_depth: int) -> Manifest | None:
        return self.manager.squash(max_depth=max_depth)


__all__ = ["LayerStackClient"]
