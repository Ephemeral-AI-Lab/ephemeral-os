"""Plugin-agnostic, lease-backed workspace projection."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sandbox.layer_stack.manifest import manifest_root_hash
from sandbox.layer_stack.stack import LayerStack
from sandbox.occ.layer_stack_client import LayerStackPortAdapter
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
    """Layer-stack projection used by stateful plugin runtimes."""

    def __init__(
        self,
        layer_stack_root: str | Path,
        *,
        manager: LayerStack | None = None,
    ) -> None:
        self._layer_stack_root = Path(layer_stack_root).resolve()
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
            LayerStackPortAdapter(self._manager),
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
