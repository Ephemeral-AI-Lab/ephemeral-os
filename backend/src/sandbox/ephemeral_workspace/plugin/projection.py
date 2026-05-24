"""Plugin-agnostic, lease-backed workspace projection.

Stateful plugins (e.g. LSP) need a current filesystem view, but the workspace
truth is the active layer-stack manifest, not the mutable provider workspace
directory. This module exposes a ``manifest_key`` so plugin sessions can detect
when the manifest changes and refresh their state against the latest snapshot.

Lives under ``sandbox.ephemeral_workspace.plugin`` rather than the LSP catalog
so future stateful plugins reuse it. It MUST stay plugin-agnostic — no
plugin-name string switches, no LSP-specific code paths.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sandbox.layer_stack.manifest import manifest_root_hash
from sandbox.layer_stack.stack import LayerStack
from sandbox.occ.layer_stack_client import LayerStackClient
from sandbox.overlay import lifecycle as overlay_lifecycle
from sandbox.overlay.handle import OverlayHandle

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
    manifest_version: int
    root_hash: str
    manifest: object | None
    layer_paths: tuple[str, ...] | None
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
    leases the active manifest and returns a :class:`ProjectionHandle` keyed by
    ``manifest_key``. Plugin runtime code can use that key to decide whether a
    long-lived session is already current or must reconcile itself with the
    latest projection.
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
        result = self._manager.prepare_workspace_snapshot(
            owner_request_id=owner_request_id,
        )
        return ProjectionHandle(
            lease_id=result.lease_id,
            manifest_key=build_manifest_key(
                result.root_hash, result.manifest_version
            ),
            manifest_version=result.manifest_version,
            root_hash=result.root_hash,
            manifest=getattr(result, "manifest", None),
            layer_paths=getattr(result, "layer_paths", None),
            _manager=self._manager,
        )

    def acquire_overlay(
        self,
        owner_request_id: str,
        *,
        workspace_root: str,
    ) -> OverlayHandle:
        return overlay_lifecycle.acquire(
            LayerStackClient(self._manager),
            invocation_id=owner_request_id,
            workspace_root=workspace_root,
        )

    def active_manifest_key(self) -> str:
        manifest = self._manager.read_active_manifest()
        return build_manifest_key(
            manifest_root_hash(manifest), manifest.version
        )

    def active_lease_count(self) -> int:
        return self._manager.active_lease_count()
