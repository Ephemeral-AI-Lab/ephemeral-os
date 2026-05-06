"""Client protocols consumed by guarded command execution."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol


class WorkspaceSnapshotLease(Protocol):
    lease_id: str
    manifest_version: int
    root_hash: str
    lowerdir: str
    cache_hit: bool
    materialized_byte_count: int
    timings: dict[str, float]


class WorkspaceLeaseClient(Protocol):
    """Layer-stack lease/snapshot client used by command execution."""

    def prepare_workspace_snapshot(
        self,
        *,
        workspace_ref: str,
        request_id: str,
        ttl_seconds: float | None = None,
    ) -> WorkspaceSnapshotLease: ...

    def release_lease(self, *, workspace_ref: str, lease_id: str) -> bool: ...


class OCCMutationClient(Protocol):
    """OCC mutation client used for shell-capture submission."""

    async def apply_changeset(
        self,
        typed_changes: Sequence[object],
        *,
        snapshot: object | None = None,
        options: object | None = None,
        workspace_ref: str | None = None,
    ) -> object: ...


__all__ = [
    "OCCMutationClient",
    "WorkspaceLeaseClient",
    "WorkspaceSnapshotLease",
]
