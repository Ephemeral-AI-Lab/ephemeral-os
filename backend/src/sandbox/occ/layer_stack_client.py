"""In-process LayerStack port adapter for OCC and workspace pipelines."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager
from pathlib import Path

from sandbox.layer_stack.commit_staging import CommitStagingArea
from sandbox.layer_stack.lease import WorkspaceLease
from sandbox.layer_stack.manifest import Manifest
from sandbox.layer_stack.stack import (
    LayerStackSnapshotLease,
    LayerStack,
)
from sandbox.occ.ports import LayerCommitTransaction


class LayerStackPortAdapter:
    """Adapter from the in-process layer-stack manager to OCC/pipeline ports.

    Forwards OCC port calls to the manager and adapts the per-workspace
    lease API onto the manager's positional signature.
    """

    def __init__(self, manager: LayerStack) -> None:
        self.manager = manager

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

    def commit_transaction(self) -> AbstractContextManager[LayerCommitTransaction]:
        return self.manager.commit_transaction()

    def allocate_commit_staging(self, request_id: str) -> CommitStagingArea:
        return self.manager.allocate_commit_staging(request_id)

    def drop_commit_staging(self, staging_id: str) -> None:
        self.manager.drop_commit_staging(staging_id)

    def prepare_workspace_snapshot(
        self,
        *,
        request_id: str,
    ) -> LayerStackSnapshotLease:
        return self.manager.prepare_workspace_snapshot(
            request_id,
        )

    def release_lease(self, *, lease_id: str) -> bool:
        return self.manager.release_lease(lease_id)

    def can_squash(self, *, max_depth: int) -> bool:
        return self.manager.can_squash(max_depth=max_depth)

    def squash(self, *, max_depth: int) -> Manifest | None:
        return self.manager.squash(max_depth=max_depth)


class LayerStackClient(LayerStackPortAdapter):
    """Legacy adapter name preserving the previous direct-import method surface.

    New code should use :class:`LayerStackPortAdapter`; these extra forwarders
    stay only because ``LayerStackClient`` is exported from this module.
    """

    def acquire_snapshot_lease(self, owner_request_id: str) -> WorkspaceLease:
        return self.manager.acquire_snapshot_lease(owner_request_id)

    def iter_paths(self, manifest: Manifest) -> Iterator[str]:
        return self.manager.iter_paths(manifest)

    def materialize(
        self,
        destination: str | Path,
        manifest: Manifest,
    ) -> None:
        self.manager.materialize(destination, manifest)

    def flush_to_workspace(
        self,
        *,
        workspace_root: str | Path,
        timings: dict[str, float] | None = None,
    ) -> Manifest:
        return self.manager.flush_to_workspace(
            workspace_root=workspace_root,
            timings=timings,
        )


__all__ = ["LayerStackClient", "LayerStackPortAdapter"]
