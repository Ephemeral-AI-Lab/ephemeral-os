"""Plugin-agnostic, lease-backed workspace projection."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sandbox.layer_stack.manifest import manifest_root_hash
from sandbox.layer_stack.stack import LayerStack
from sandbox.occ.layer_stack_adapter import LayerStackPortAdapter
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
    _layer_stack: LayerStack
    _released: bool = False

    def release(self) -> None:
        """Release the underlying lease. Idempotent."""
        if self._released:
            return
        self._released = True
        self._layer_stack.release_lease(self.lease_id)

    @property
    def released(self) -> bool:
        return self._released


class WorkspaceProjection:
    """Layer-stack projection used by stateful plugin runtimes."""

    def __init__(
        self,
        layer_stack_root: str | Path,
        *,
        layer_stack: LayerStack | None = None,
    ) -> None:
        self._layer_stack_root = Path(layer_stack_root).resolve()
        self._layer_stack = (
            layer_stack if layer_stack is not None else LayerStack(self._layer_stack_root)
        )

    @property
    def layer_stack_root(self) -> Path:
        return self._layer_stack_root

    def acquire(self, invocation_id: str) -> ProjectionHandle:
        result = self._layer_stack.acquire_snapshot(
            owner_request_id=invocation_id,
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
            _layer_stack=self._layer_stack,
        )

    def acquire_overlay(
        self,
        invocation_id: str,
        *,
        workspace_root: str,
    ) -> OverlayHandle:
        return overlay_lifecycle.acquire(
            LayerStackPortAdapter(self._layer_stack),
            invocation_id=invocation_id,
            workspace_root=workspace_root,
        )

    def active_manifest_key(self) -> str:
        manifest = self._layer_stack.read_active_manifest()
        return build_manifest_key(
            manifest_root_hash(manifest), manifest.version
        )

    def active_lease_count(self) -> int:
        return self._layer_stack.active_lease_count()
