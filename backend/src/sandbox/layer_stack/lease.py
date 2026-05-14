"""Exact layer-ref lease registry for frozen layer-stack snapshots."""

from __future__ import annotations

import threading
import time
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

from sandbox.layer_stack.manifest import LayerRef, Manifest


@dataclass(frozen=True)
class WorkspaceLease:
    lease_id: str
    manifest: Manifest
    owner_request_id: str
    acquired_at: float


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

    def acquire(
        self,
        manifest: Manifest,
        owner_request_id: str,
    ) -> WorkspaceLease:
        if not owner_request_id:
            raise ValueError("owner_request_id must not be empty")
        with self._lock:
            lease = WorkspaceLease(
                lease_id=self._id_factory(),
                manifest=manifest,
                owner_request_id=owner_request_id,
                acquired_at=self._clock(),
            )
            self._leases[lease.lease_id] = lease
            self._refcounts.update(manifest.layers)
            return lease

    def release(self, lease_id: str) -> WorkspaceLease | None:
        with self._lock:
            lease = self._leases.pop(lease_id, None)
            if lease is None:
                return None
            self._refcounts -= Counter(lease.manifest.layers)
            return lease

    def pinned_layers(self) -> tuple[LayerRef, ...]:
        with self._lock:
            return tuple(sorted(self._refcounts))

    def active_count(self) -> int:
        with self._lock:
            return len(self._leases)


__all__ = ["LeaseRegistry", "WorkspaceLease"]
