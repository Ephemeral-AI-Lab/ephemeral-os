"""Public storage facade for sandbox layer-stack state."""

from __future__ import annotations

import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
import shutil
from types import TracebackType

from sandbox.layer_stack.changes import LayerChange
from sandbox.layer_stack.lease_registry import LeaseRegistry, WorkspaceLease
from sandbox.layer_stack.manifest import (
    LAYERS_DIR,
    STAGING_DIR,
    LayerRef,
    Manifest,
    empty_manifest,
    manifest_path,
    read_manifest,
    write_manifest_atomic,
)
from sandbox.layer_stack.merged_view import MergedView
from sandbox.layer_stack.metrics import LowerdirCacheMetrics
from sandbox.layer_stack.publisher import LayerPublisher
from sandbox.layer_stack.snapshot_cache import (
    MaterializedSnapshotCache,
    manifest_root_hash,
)
from sandbox.layer_stack.squash import SquashWorker, manifest_still_ends_with


@dataclass(frozen=True)
class PrepareWorkspaceSnapshotResult:
    lease_id: str
    manifest_version: int
    root_hash: str
    manifest: Manifest
    lowerdir: str
    cache_hit: bool
    materialized_byte_count: int
    timings: dict[str, float]
    cache_policy: str = "enabled"
    transient_lowerdir: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "lease_id": self.lease_id,
            "manifest_version": self.manifest_version,
            "root_hash": self.root_hash,
            "manifest": self.manifest.to_dict(),
            "lowerdir": self.lowerdir,
            "cache_hit": self.cache_hit,
            "materialized_byte_count": self.materialized_byte_count,
            "timings": dict(self.timings),
            "cache_policy": self.cache_policy,
            "transient_lowerdir": self.transient_lowerdir,
        }


class LayerStackManager:
    """Coordinates active manifests, snapshot leases, reads, and publishes."""

    def __init__(self, storage_root: str | Path) -> None:
        self.storage_root = Path(storage_root)
        self.storage_root.mkdir(parents=True, exist_ok=True)
        (self.storage_root / LAYERS_DIR).mkdir(exist_ok=True)
        (self.storage_root / STAGING_DIR).mkdir(exist_ok=True)

        self._manifest_file = manifest_path(self.storage_root)
        if not self._manifest_file.exists():
            write_manifest_atomic(self._manifest_file, empty_manifest())

        self._lock = threading.RLock()
        self._leases = LeaseRegistry()
        self._view = MergedView(self.storage_root)
        self._publisher = LayerPublisher(self.storage_root)
        self._snapshot_cache = MaterializedSnapshotCache(self.storage_root)
        self._squash = SquashWorker(self.storage_root)

    def read_active_manifest(self) -> Manifest:
        with self._lock:
            return read_manifest(self._manifest_file)

    def acquire_snapshot_lease(self, owner_request_id: str) -> WorkspaceLease:
        with self._lock:
            return self._leases.acquire(
                self.read_active_manifest(),
                owner_request_id,
            )

    def prepare_workspace_snapshot(
        self,
        owner_request_id: str,
    ) -> PrepareWorkspaceSnapshotResult:
        total_start = time.perf_counter()
        with self._lock:
            manifest = read_manifest(self._manifest_file)
            root_hash = manifest_root_hash(manifest)
            lease = self._leases.acquire(
                manifest,
                owner_request_id,
            )
            try:
                lookup = self._snapshot_cache.get_or_create(
                    manifest,
                    root_hash=root_hash,
                )
                self._leases.pin_lowerdir(
                    lease.lease_id,
                    lookup.snapshot.lowerdir,
                )
            except Exception:
                self._leases.release(lease.lease_id)
                raise

            timings = {
                **lookup.timings,
                "layer_stack.prepare_workspace_snapshot.total_s": (
                    time.perf_counter() - total_start
                ),
            }
            return PrepareWorkspaceSnapshotResult(
                lease_id=lease.lease_id,
                manifest_version=manifest.version,
                root_hash=root_hash,
                manifest=manifest,
                lowerdir=lookup.snapshot.lowerdir,
                cache_hit=lookup.cache_hit,
                materialized_byte_count=lookup.snapshot.byte_count,
                timings=timings,
            )

    def release_lease(self, lease_id: str) -> bool:
        with self._lock:
            lease = self._leases.release(lease_id)
            if lease is None:
                return False
            active_manifest = read_manifest(self._manifest_file)
            self._remove_unreferenced_layers(
                lease.manifest.layers,
                current_manifest=active_manifest,
            )
            lowerdir = lease.materialized_lowerdir
            if (
                lowerdir
                and lowerdir not in self._leases.pinned_lowerdirs()
                and lease.manifest != active_manifest
            ):
                self._snapshot_cache.remove_lowerdir(lowerdir)
            return True

    def pinned_layers(self) -> tuple[LayerRef, ...]:
        return self._leases.pinned_layers()

    def pinned_lowerdirs(self) -> tuple[str, ...]:
        return self._leases.pinned_lowerdirs()

    def lowerdir_cache_metrics(self) -> LowerdirCacheMetrics:
        return self._snapshot_cache.metrics

    def materialized_lowerdir_count(self) -> int:
        return self._snapshot_cache.materialized_count()

    def active_lease_count(self) -> int:
        return self._leases.active_count()

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

    def squash(self, *, max_depth: int) -> Manifest | None:
        plan = self._squash.plan(self.read_active_manifest(), max_depth=max_depth)
        if plan is None:
            return None

        checkpoint = self._squash.build_checkpoint(plan)
        checkpoint_committed = False
        try:
            with self._lock:
                current = read_manifest(self._manifest_file)
                if not manifest_still_ends_with(
                    current,
                    plan.suffix_to_checkpoint,
                ):
                    return None
                live_prefix = current.layers[: -len(plan.suffix_to_checkpoint)]
                new_manifest = Manifest(
                    version=current.version + 1,
                    layers=(*live_prefix, checkpoint),
                )
                write_manifest_atomic(self._manifest_file, new_manifest)
                checkpoint_committed = True
                self._remove_unreferenced_layers(
                    plan.suffix_to_checkpoint,
                    current_manifest=new_manifest,
                )
            return new_manifest
        finally:
            if not checkpoint_committed:
                self._squash.discard_checkpoint(checkpoint)

    def _layer_path(self, layer: LayerRef) -> Path:
        path = Path(layer.path)
        if not path.is_absolute():
            path = self.storage_root / path
        return path

    def _remove_unreferenced_layers(
        self,
        candidates: Sequence[LayerRef],
        *,
        current_manifest: Manifest,
    ) -> tuple[str, ...]:
        active_layers = set(current_manifest.layers)
        pinned_layers = set(self._leases.pinned_layers())
        removed: list[str] = []
        for layer in sorted(set(candidates), key=lambda item: item.layer_id):
            if layer in active_layers or layer in pinned_layers:
                continue
            _remove_path(self._layer_path(layer))
            _layer_digest_path(self.storage_root, layer.layer_id).unlink(missing_ok=True)
            removed.append(layer.layer_id)
        return tuple(removed)


class LayerStackTransaction:
    """Process-local active-manifest transaction shell."""

    def __init__(self, manager: LayerStackManager) -> None:
        self._manager = manager
        self._manifest: Manifest | None = None
        self._entered = False
        self._lock_acquired_at: float | None = None
        self._lock_held_s = 0.0
        self._lock_wait_s = 0.0

    def __enter__(self) -> "LayerStackTransaction":
        wait_start = time.perf_counter()
        self._manager._lock.acquire()
        acquired_at = time.perf_counter()
        self._lock_wait_s = acquired_at - wait_start
        self._lock_acquired_at = acquired_at
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
        if self._lock_acquired_at is not None:
            self._lock_held_s = time.perf_counter() - self._lock_acquired_at
            self._lock_acquired_at = None
        self._manager._lock.release()

    def snapshot(self) -> Manifest:
        return self._require_manifest()

    def publish_layer(
        self,
        changes: Sequence[LayerChange],
        *,
        timings: dict[str, float] | None = None,
    ) -> Manifest:
        current = self._require_manifest()
        new_manifest = self._manager._publisher.publish_layer_locked(
            tuple(changes),
            expected_manifest=current,
            timings=timings,
        )
        self._manifest = new_manifest
        return new_manifest

    @property
    def lock_wait_s(self) -> float:
        return self._lock_wait_s

    @property
    def lock_held_s(self) -> float:
        if self._lock_acquired_at is not None:
            return time.perf_counter() - self._lock_acquired_at
        return self._lock_held_s

    def _require_manifest(self) -> Manifest:
        if not self._entered or self._manifest is None:
            raise RuntimeError("layer-stack transaction is not active")
        return self._manifest


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)


def _layer_digest_path(storage_root: Path, layer_id: str) -> Path:
    return storage_root / ".layer-metadata" / f"{layer_id}.digest"
