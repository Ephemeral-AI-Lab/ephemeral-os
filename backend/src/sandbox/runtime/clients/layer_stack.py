"""In-process layer-stack client implementing narrow runtime/OCC ports."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ContextManager

from sandbox.layer_stack.manifest import Manifest
from sandbox.layer_stack.stack_manager import PrepareWorkspaceSnapshotResult
from sandbox.occ.ports import (
    CommitStagingArea,
    CommitTransaction,
    OccLayerStackPorts,
    ensure_layer_stack_ports,
)
from sandbox.runtime.layer_stack_server import get_layer_stack_manager

if TYPE_CHECKING:  # pragma: no cover
    from sandbox.layer_stack.stack_manager import LayerStackManager


class LayerStackClient:
    """Client boundary around the current in-process layer-stack manager."""

    def __init__(self, layer_stack_root: str | Path | object) -> None:
        if isinstance(layer_stack_root, (str, Path)):
            self._manager: object = get_layer_stack_manager(layer_stack_root)
        else:
            self._manager = layer_stack_root
        self._ports: OccLayerStackPorts = ensure_layer_stack_ports(self._manager)

    @property
    def manager(self) -> "LayerStackManager":
        return self._manager  # type: ignore[return-value]

    @property
    def storage_root(self) -> Path:
        return Path(getattr(self._ports, "storage_root"))

    @property
    def snapshot_cache_root(self) -> Path:
        return self._ports.snapshot_cache_root

    def get_active_manifest(self, workspace_ref: str = "") -> Manifest:
        return self._ports.get_active_manifest(workspace_ref)

    def read_bytes(
        self,
        path: str,
        snapshot: Manifest,
        *,
        workspace_ref: str = "",
    ) -> tuple[bytes | None, bool]:
        return self._ports.read_bytes(path, snapshot, workspace_ref=workspace_ref)

    def read_text(
        self,
        path: str,
        snapshot: Manifest,
        *,
        workspace_ref: str = "",
    ) -> tuple[str, bool]:
        return self._ports.read_text(path, snapshot, workspace_ref=workspace_ref)

    def materialize_snapshot(
        self,
        destination: str | Path,
        snapshot: Manifest,
        *,
        workspace_ref: str = "",
    ) -> None:
        self._ports.materialize_snapshot(
            destination,
            snapshot,
            workspace_ref=workspace_ref,
        )

    def allocate_commit_staging(
        self,
        workspace_ref: str,
        request_id: str,
    ) -> CommitStagingArea:
        return self._ports.allocate_commit_staging(workspace_ref, request_id)

    def drop_commit_staging(self, workspace_ref: str, staging_id: str) -> None:
        self._ports.drop_commit_staging(workspace_ref, staging_id)

    def commit_transaction(
        self,
        workspace_ref: str = "",
    ) -> ContextManager[CommitTransaction]:
        return self._ports.commit_transaction(workspace_ref)

    def prepare_workspace_snapshot(
        self,
        *,
        workspace_ref: str = "",
        request_id: str,
        ttl_seconds: float | None = None,
    ) -> PrepareWorkspaceSnapshotResult:
        del workspace_ref, ttl_seconds
        return self.manager.prepare_workspace_snapshot(request_id)

    def release_lease(self, *, workspace_ref: str = "", lease_id: str) -> bool:
        del workspace_ref
        return self.manager.release_lease(lease_id)


__all__ = ["LayerStackClient"]
