"""Plugin-agnostic, lease-backed workspace projection.

Stateful plugins (e.g. LSP) need a real on-disk filesystem tree to point a
language server at, but the workspace truth is the active layer-stack
manifest — not the mutable provider workspace directory. This module
materializes the active manifest into a transient lowerdir and exposes a
``manifest_key`` so plugin sessions can detect when the manifest changes and
refresh their state against the new snapshot.

Lives under ``sandbox/plugin/`` rather than the LSP catalog so future stateful
plugins reuse it. It MUST stay plugin-agnostic — no plugin-name string
switches, no LSP-specific code paths.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from sandbox.layer_stack.stack import LayerStack, PrepareWorkspaceSnapshotResult
from sandbox.layer_stack.manifest import manifest_root_hash
from sandbox.layer_stack.view import LayerStackStorageError

if TYPE_CHECKING:  # pragma: no cover
    pass

__all__ = [
    "ProjectionHandle",
    "WorkspaceProjection",
    "build_manifest_key",
]


def build_manifest_key(root_hash: str, manifest_version: int) -> str:
    """Stable key for caching plugin sessions across manifest revisions."""
    return f"{root_hash}@{manifest_version}"


@dataclass
class ProjectionHandle:
    """Lease-backed view of the active layer-stack manifest."""

    lease_id: str
    manifest_key: str
    lowerdir: str
    manifest_version: int
    root_hash: str
    _manager: LayerStack
    _released: bool = False

    def release(self) -> None:
        """Release the underlying lease. Idempotent."""
        if self._released:
            return
        self._released = True
        self._manager.release_lease(self.lease_id)

    @property
    def released(self) -> bool:
        return self._released


class WorkspaceProjection:
    """Wrapper around :class:`LayerStack` for plugin runtime ops.

    Constructor takes a layer_stack_root (filesystem path); each acquire call
    materializes the active manifest into a transient lowerdir and returns a
    :class:`ProjectionHandle` keyed by ``manifest_key``. Plugin runtime code can
    use that key to decide whether a long-lived session is already current or
    must reconcile itself with the latest projection.
    """

    def __init__(
        self,
        layer_stack_root: str | Path,
        *,
        manager: LayerStack | None = None,
    ) -> None:
        self._layer_stack_root = Path(layer_stack_root).resolve()
        # Reuse the daemon's cached LayerStack when one is injected so
        # the plugin path and the OCC backend share a single writer flock +
        # transaction RLock. Constructing a fresh manager here is the legacy
        # path retained for unit tests and out-of-daemon callers.
        self._manager = (
            manager if manager is not None else LayerStack(self._layer_stack_root)
        )

    @property
    def layer_stack_root(self) -> Path:
        return self._layer_stack_root

    def acquire(self, owner_request_id: str) -> ProjectionHandle:
        result = self._prepare_snapshot_with_retry(owner_request_id)
        return ProjectionHandle(
            lease_id=result.lease_id,
            manifest_key=build_manifest_key(
                result.root_hash, result.manifest_version
            ),
            lowerdir=result.lowerdir,
            manifest_version=result.manifest_version,
            root_hash=result.root_hash,
            _manager=self._manager,
        )

    def _prepare_snapshot_with_retry(
        self, owner_request_id: str
    ) -> PrepareWorkspaceSnapshotResult:
        try:
            return self._manager.prepare_workspace_snapshot(
                owner_request_id=owner_request_id,
            )
        except (FileNotFoundError, LayerStackStorageError):
            return self._manager.prepare_workspace_snapshot(
                owner_request_id=owner_request_id,
            )

    def active_manifest_key(self) -> str:
        manifest = self._manager.read_active_manifest()
        return build_manifest_key(
            manifest_root_hash(manifest), manifest.version
        )

    def active_lease_count(self) -> int:
        return self._manager.active_lease_count()
