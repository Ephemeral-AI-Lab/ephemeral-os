"""Exact layer-ref lease registry for frozen layer-stack snapshots."""

from __future__ import annotations

import threading
import time
import uuid
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace

from sandbox.layer_stack.manifest import LayerRef, Manifest


@dataclass(frozen=True)
class WorkspaceLease:
    lease_id: str
    manifest: Manifest
    owner_request_id: str
    acquired_at: float
    root_hash: str = ""
    materialized_lowerdir: str = ""
    workspace_ref: str = ""
    expires_at: float | None = None

    @property
    def owner_id(self) -> str:
        return self.owner_request_id

    @property
    def manifest_version(self) -> int:
        return self.manifest.version


Lease = WorkspaceLease


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
        owner_id: str,
        *,
        root_hash: str = "",
        materialized_lowerdir: str = "",
        workspace_ref: str = "",
        ttl_seconds: float | None = None,
    ) -> WorkspaceLease:
        if not owner_id:
            raise ValueError("owner_id must not be empty")
        if ttl_seconds is not None and ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive when provided")
        with self._lock:
            acquired_at = self._clock()
            lease = WorkspaceLease(
                lease_id=self._id_factory(),
                manifest=manifest,
                owner_request_id=owner_id,
                acquired_at=acquired_at,
                root_hash=root_hash,
                materialized_lowerdir=materialized_lowerdir,
                workspace_ref=workspace_ref,
                expires_at=(
                    acquired_at + ttl_seconds if ttl_seconds is not None else None
                ),
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

    def expire_older_than(
        self,
        max_age_seconds: float,
        *,
        now: float | None = None,
    ) -> tuple[WorkspaceLease, ...]:
        if max_age_seconds < 0:
            raise ValueError("max_age_seconds must be non-negative")
        cutoff = (self._clock() if now is None else now) - max_age_seconds
        with self._lock:
            expired_ids = (
                lease.lease_id
                for lease in self._ordered_leases_locked()
                if lease.acquired_at <= cutoff
            )
            return self._release_many_locked(expired_ids)

    def sweep_dead_owners(
        self,
        live_owner_ids: Iterable[str],
    ) -> tuple[WorkspaceLease, ...]:
        live = set(live_owner_ids)
        with self._lock:
            dead_ids = (
                lease.lease_id
                for lease in self._ordered_leases_locked()
                if lease.owner_id not in live
            )
            return self._release_many_locked(dead_ids)

    def refcount(self, layer: LayerRef) -> int:
        with self._lock:
            return self._refcounts.get(layer, 0)

    def pinned_layers(self) -> tuple[LayerRef, ...]:
        with self._lock:
            return tuple(sorted(self._refcounts))

    def lowerdir_refcount(self, lowerdir: str) -> int:
        with self._lock:
            return self._lowerdir_refcounts.get(lowerdir, 0)

    def pinned_lowerdirs(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._lowerdir_refcounts))

    def active_leases(self) -> tuple[WorkspaceLease, ...]:
        with self._lock:
            return self._ordered_leases_locked()

    def _ordered_leases_locked(self) -> tuple[WorkspaceLease, ...]:
        return tuple(sorted(self._leases.values(), key=lambda lease: lease.acquired_at))

    def _release_many_locked(
        self,
        lease_ids: Iterable[str],
    ) -> tuple[WorkspaceLease, ...]:
        released: list[WorkspaceLease] = []
        for lease_id in tuple(lease_ids):
            lease = self._release_locked(lease_id)
            if lease is not None:
                released.append(lease)
        return tuple(released)

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


__all__ = ["Lease", "LeaseRegistry", "WorkspaceLease"]
