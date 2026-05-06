"""Exact layer-ref lease registry for frozen layer-stack snapshots."""

from __future__ import annotations

import threading
import time
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, replace

from sandbox.layer_stack.manifest import LayerRef, Manifest


@dataclass(frozen=True)
class WorkspaceLease:
    lease_id: str
    manifest: Manifest
    owner_request_id: str
    acquired_at: float
    materialized_lowerdir: str = ""


class LeaseRegistry:
    """Tracks active snapshot leases and exact pinned layer refs."""

    def __init__(
        self,
        *,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._id_factory = id_factory or (lambda: uuid.uuid4().hex)
        self._clock = clock or time.time
        self._lock = threading.RLock()
        self._leases: dict[str, WorkspaceLease] = {}
        self._refcounts: Counter[LayerRef] = Counter()
        self._lowerdir_refcounts: Counter[str] = Counter()

    def acquire(
        self,
        manifest: Manifest,
        owner_request_id: str,
        *,
        materialized_lowerdir: str = "",
    ) -> WorkspaceLease:
        if not owner_request_id:
            raise ValueError("owner_request_id must not be empty")
        with self._lock:
            acquired_at = self._clock()
            lease = WorkspaceLease(
                lease_id=self._id_factory(),
                manifest=manifest,
                owner_request_id=owner_request_id,
                acquired_at=acquired_at,
                materialized_lowerdir=materialized_lowerdir,
            )
            self._leases[lease.lease_id] = lease
            self._refcounts.update(manifest.layers)
            if materialized_lowerdir:
                self._lowerdir_refcounts.update((materialized_lowerdir,))
            return lease

    def pin_lowerdir(self, lease_id: str, lowerdir: str) -> WorkspaceLease:
        if not lowerdir:
            raise ValueError("lowerdir must not be empty")
        with self._lock:
            lease = self._leases.get(lease_id)
            if lease is None:
                raise KeyError(f"unknown lease: {lease_id}")
            if lease.materialized_lowerdir == lowerdir:
                return lease
            if lease.materialized_lowerdir:
                self._unpin_lowerdir_locked(lease.materialized_lowerdir)
            updated = replace(lease, materialized_lowerdir=lowerdir)
            self._leases[lease_id] = updated
            self._lowerdir_refcounts.update((lowerdir,))
            return updated

    def release(self, lease_id: str) -> WorkspaceLease | None:
        with self._lock:
            return self._release_locked(lease_id)

    def pinned_layers(self) -> tuple[LayerRef, ...]:
        with self._lock:
            return tuple(sorted(self._refcounts))

    def pinned_lowerdirs(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._lowerdir_refcounts))

    def active_count(self) -> int:
        with self._lock:
            return len(self._leases)

    def _release_locked(self, lease_id: str) -> WorkspaceLease | None:
        lease = self._leases.pop(lease_id, None)
        if lease is None:
            return None
        for layer in lease.manifest.layers:
            self._refcounts[layer] -= 1
            if self._refcounts[layer] <= 0:
                del self._refcounts[layer]
        if lease.materialized_lowerdir:
            self._unpin_lowerdir_locked(lease.materialized_lowerdir)
        return lease

    def _unpin_lowerdir_locked(self, lowerdir: str) -> None:
        self._lowerdir_refcounts[lowerdir] -= 1
        if self._lowerdir_refcounts[lowerdir] <= 0:
            del self._lowerdir_refcounts[lowerdir]


__all__ = ["LeaseRegistry", "WorkspaceLease"]
