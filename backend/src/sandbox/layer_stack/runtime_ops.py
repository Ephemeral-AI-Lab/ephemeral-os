"""Boundary-local runtime operation shapes for layer-stack callers."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sandbox.layer_stack.changes import LayerChange
from sandbox.layer_stack.lease_registry import Lease
from sandbox.layer_stack.manifest import Manifest
from sandbox.layer_stack.stack_manager import LayerStackManager


@dataclass(frozen=True)
class LayerStackReadResult:
    path: str
    exists: bool
    content: bytes | None


class LayerStackRuntimeOps:
    """Small typed wrapper used before any sandbox runtime wire protocol exists."""

    def __init__(self, manager: LayerStackManager) -> None:
        self._manager = manager

    def acquire_snapshot_lease(self, owner_id: str) -> Lease:
        return self._manager.acquire_snapshot_lease(owner_id)

    def release_lease(self, lease_id: str) -> bool:
        return self._manager.release_lease(lease_id)

    def read_bytes(
        self,
        path: str,
        manifest: Manifest | None = None,
    ) -> LayerStackReadResult:
        content, exists = self._manager.read_bytes(path, manifest=manifest)
        return LayerStackReadResult(path=path, exists=exists, content=content)

    def publish_layer(self, changes: Sequence[LayerChange]) -> Manifest:
        return self._manager.publish_changes(changes)

