"""Narrow layer-stack role ports consumed by OCC."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import ContextManager, Protocol

from sandbox.layer_stack.changes import LayerChange
from sandbox.layer_stack.manifest import Manifest
from sandbox.layer_stack.staging import CommitStagingArea


@dataclass(frozen=True)
class WorkspaceBindingSnapshot:
    workspace_ref: str
    workspace_root: str
    layer_stack_root: str


class SnapshotReader(Protocol):
    """Read immutable snapshot content without exposing storage layout."""

    def read_active_manifest(self) -> Manifest: ...

    def read_bytes(
        self,
        path: str,
        manifest: Manifest | None = None,
    ) -> tuple[bytes | None, bool]: ...

    def read_text(
        self,
        path: str,
        manifest: Manifest | None = None,
    ) -> tuple[str, bool]: ...


class SnapshotMaterializer(Protocol):
    """Materialize a snapshot into a caller-owned directory."""

    def materialize(
        self,
        destination: str | Path,
        manifest: Manifest | None = None,
    ) -> None: ...

    @property
    def gitignore_cache_root(self) -> Path:
        """Working root for the gitignore oracle's per-version git workspaces."""
        ...


class CommitStagingStore(Protocol):
    """Allocate and drop OCC-owned staging directories."""

    def allocate_commit_staging(self, request_id: str) -> CommitStagingArea: ...

    def drop_commit_staging(self, staging_id: str) -> None: ...


class CommitTransaction(Protocol):
    @property
    def lock_wait_s(self) -> float: ...

    @property
    def lock_held_s(self) -> float: ...

    def snapshot(self) -> Manifest: ...

    def publish_layer(
        self,
        changes: Sequence[LayerChange],
        *,
        timings: dict[str, float] | None = None,
    ) -> Manifest: ...


class CommitPublisher(Protocol):
    """Publish accepted staged changes through the storage CAS primitive."""

    def commit_transaction(self) -> ContextManager[CommitTransaction]: ...


class WorkspaceBindingReader(Protocol):
    """Fail-closed binding lookup used by OCC-facing clients."""

    def require_workspace_binding(
        self,
        workspace_ref: str,
    ) -> WorkspaceBindingSnapshot: ...


class OccLayerStackPorts(
    SnapshotReader,
    SnapshotMaterializer,
    CommitStagingStore,
    CommitPublisher,
    Protocol,
):
    """Combined in-process migration shape for the current OCC service."""


__all__ = [
    "CommitPublisher",
    "CommitStagingArea",
    "CommitStagingStore",
    "CommitTransaction",
    "OccLayerStackPorts",
    "SnapshotMaterializer",
    "SnapshotReader",
    "WorkspaceBindingReader",
    "WorkspaceBindingSnapshot",
]
