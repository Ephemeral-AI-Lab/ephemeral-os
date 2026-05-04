"""Public storage facade for sandbox layer-stack state."""

from __future__ import annotations

import threading
from collections.abc import Sequence
from pathlib import Path
from types import TracebackType

from sandbox.layer_stack.changes import LayerChange
from sandbox.layer_stack.lease_registry import Lease, LeaseRegistry
from sandbox.layer_stack.manifest import (
    LAYERS_DIR,
    STAGING_DIR,
    LayerRef,
    Manifest,
    manifest_path,
    read_manifest,
    write_manifest_atomic,
)
from sandbox.layer_stack.merged_view import MergedView
from sandbox.layer_stack.publisher import LayerPublisher


class LayerStackManager:
    """Coordinates active manifests, snapshot leases, reads, and publishes."""

    def __init__(self, storage_root: str | Path) -> None:
        self.storage_root = Path(storage_root)
        self.storage_root.mkdir(parents=True, exist_ok=True)
        (self.storage_root / LAYERS_DIR).mkdir(exist_ok=True)
        (self.storage_root / STAGING_DIR).mkdir(exist_ok=True)

        self._manifest_file = manifest_path(self.storage_root)
        if not self._manifest_file.exists():
            write_manifest_atomic(self._manifest_file, read_manifest(self._manifest_file))

        self._lock = threading.RLock()
        self._leases = LeaseRegistry()
        self._view = MergedView(self.storage_root)
        self._publisher = LayerPublisher(self.storage_root, self._manifest_file)

    def read_active_manifest(self) -> Manifest:
        with self._lock:
            return read_manifest(self._manifest_file)

    def acquire_snapshot_lease(self, owner_id: str) -> Lease:
        with self._lock:
            return self._leases.acquire(self.read_active_manifest(), owner_id)

    def release_lease(self, lease_id: str) -> bool:
        with self._lock:
            return self._leases.release(lease_id) is not None

    def lease_refcount(self, layer: LayerRef) -> int:
        return self._leases.refcount(layer)

    def pinned_layers(self) -> tuple[LayerRef, ...]:
        return self._leases.pinned_layers()

    def read_bytes(
        self,
        path: str,
        manifest: Manifest | None = None,
    ) -> tuple[bytes | None, bool]:
        return self._view.read_bytes(path, manifest or self.read_active_manifest())

    def read_text(
        self,
        path: str,
        manifest: Manifest | None = None,
    ) -> tuple[str, bool]:
        return self._view.read_text(path, manifest or self.read_active_manifest())

    def read_symlink(
        self,
        path: str,
        manifest: Manifest | None = None,
    ) -> tuple[str, bool]:
        return self._view.read_symlink(path, manifest or self.read_active_manifest())

    def list_dir(
        self,
        path: str = "",
        manifest: Manifest | None = None,
    ) -> tuple[str, ...]:
        return self._view.list_dir(path, manifest or self.read_active_manifest())

    def materialize(self, destination: str | Path, manifest: Manifest | None = None) -> None:
        self._view.materialize(destination, manifest or self.read_active_manifest())

    def commit_transaction(self) -> "LayerStackTransaction":
        return LayerStackTransaction(self)

    def publish_changes(self, changes: Sequence[LayerChange]) -> Manifest:
        with self.commit_transaction() as transaction:
            return transaction.publish_layer(changes)


class LayerStackTransaction:
    """Process-local active-manifest transaction shell."""

    def __init__(self, manager: LayerStackManager) -> None:
        self._manager = manager
        self._manifest: Manifest | None = None
        self._entered = False

    def __enter__(self) -> "LayerStackTransaction":
        self._manager._lock.acquire()
        self._entered = True
        self._manifest = read_manifest(self._manager._manifest_file)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback
        self._entered = False
        self._manifest = None
        self._manager._lock.release()

    def snapshot(self) -> Manifest:
        return self._require_manifest()

    def publish_layer(self, changes: Sequence[LayerChange]) -> Manifest:
        current = self._require_manifest()
        new_manifest = self._manager._publisher.publish_layer_locked(
            tuple(changes),
            expected_manifest=current,
        )
        self._manifest = new_manifest
        return new_manifest

    def _require_manifest(self) -> Manifest:
        if not self._entered or self._manifest is None:
            raise RuntimeError("layer-stack transaction is not active")
        return self._manifest

