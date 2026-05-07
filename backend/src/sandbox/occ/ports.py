"""Narrow layer-stack role ports consumed by OCC."""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ContextManager, Protocol, cast

from sandbox.layer_stack.changes import LayerChange
from sandbox.layer_stack.manifest import Manifest, STAGING_DIR


@dataclass(frozen=True)
class CommitStagingArea:
    staging_id: str
    path: Path


@dataclass(frozen=True)
class WorkspaceBindingSnapshot:
    workspace_ref: str
    workspace_root: str
    layer_stack_root: str


class SnapshotReader(Protocol):
    """Read immutable snapshot content without exposing storage layout."""

    def get_active_manifest(self, workspace_ref: str = "") -> Manifest: ...

    def read_bytes(
        self,
        path: str,
        snapshot: Manifest,
        *,
        workspace_ref: str = "",
    ) -> tuple[bytes | None, bool]: ...

    def read_text(
        self,
        path: str,
        snapshot: Manifest,
        *,
        workspace_ref: str = "",
    ) -> tuple[str, bool]: ...


class SnapshotMaterializer(Protocol):
    """Materialize a snapshot into a caller-owned directory."""

    def materialize_snapshot(
        self,
        destination: str | Path,
        snapshot: Manifest,
        *,
        workspace_ref: str = "",
    ) -> None: ...

    @property
    def gitignore_cache_root(self) -> Path:
        """Working root for the gitignore oracle's per-version git workspaces."""
        ...


class CommitStagingStore(Protocol):
    """Allocate and drop OCC-owned staging directories."""

    def allocate_commit_staging(
        self,
        workspace_ref: str,
        request_id: str,
    ) -> CommitStagingArea: ...

    def drop_commit_staging(self, workspace_ref: str, staging_id: str) -> None: ...


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

    def commit_transaction(
        self,
        workspace_ref: str = "",
    ) -> ContextManager[CommitTransaction]: ...


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


def ensure_layer_stack_ports(layer_stack: object) -> OccLayerStackPorts:
    """Return narrow OCC ports for an existing in-process layer-stack object."""
    if _has_narrow_ports(layer_stack):
        return cast(OccLayerStackPorts, layer_stack)
    return _LayerStackPortsAdapter(layer_stack)


def _has_narrow_ports(value: object) -> bool:
    return all(
        hasattr(value, name)
        for name in (
            "get_active_manifest",
            "read_bytes",
            "read_text",
            "materialize_snapshot",
            "gitignore_cache_root",
            "allocate_commit_staging",
            "drop_commit_staging",
            "commit_transaction",
        )
    )


class _LayerStackPortsAdapter:
    """Duck adapter for the current in-process LayerStackManager API."""

    def __init__(self, layer_stack: object) -> None:
        self._layer_stack = layer_stack

    @property
    def storage_root(self) -> Path:
        return Path(cast(Any, self._layer_stack).storage_root)

    @property
    def gitignore_cache_root(self) -> Path:
        return self.storage_root / "runtime" / "gitignore-cache"

    def get_active_manifest(self, workspace_ref: str = "") -> Manifest:
        del workspace_ref
        return cast(Any, self._layer_stack).read_active_manifest()

    def read_bytes(
        self,
        path: str,
        snapshot: Manifest,
        *,
        workspace_ref: str = "",
    ) -> tuple[bytes | None, bool]:
        del workspace_ref
        return cast(Any, self._layer_stack).read_bytes(path, snapshot)

    def read_text(
        self,
        path: str,
        snapshot: Manifest,
        *,
        workspace_ref: str = "",
    ) -> tuple[str, bool]:
        del workspace_ref
        return cast(Any, self._layer_stack).read_text(path, snapshot)

    def materialize_snapshot(
        self,
        destination: str | Path,
        snapshot: Manifest,
        *,
        workspace_ref: str = "",
    ) -> None:
        del workspace_ref
        cast(Any, self._layer_stack).materialize(destination, snapshot)

    def allocate_commit_staging(
        self,
        workspace_ref: str,
        request_id: str,
    ) -> CommitStagingArea:
        del workspace_ref
        parent = self.storage_root / STAGING_DIR
        parent.mkdir(parents=True, exist_ok=True)
        path = Path(
            tempfile.mkdtemp(
                prefix=f"occ-commit-{_safe_staging_part(request_id)}-",
                dir=str(parent),
            )
        )
        return CommitStagingArea(staging_id=path.name, path=path)

    def drop_commit_staging(self, workspace_ref: str, staging_id: str) -> None:
        del workspace_ref
        if not staging_id:
            return
        shutil.rmtree(self.storage_root / STAGING_DIR / staging_id, ignore_errors=True)

    def commit_transaction(
        self,
        workspace_ref: str = "",
    ) -> ContextManager[CommitTransaction]:
        del workspace_ref
        return cast(Any, self._layer_stack).commit_transaction()


def _safe_staging_part(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in value)
    return safe[:48] or "request"


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
    "ensure_layer_stack_ports",
]
