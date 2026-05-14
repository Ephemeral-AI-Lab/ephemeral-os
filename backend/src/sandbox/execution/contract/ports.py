"""Client protocols consumed by guarded command execution."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Protocol

from sandbox.execution.contract.request import CommandExecRequest
from sandbox.execution.contract.result import CommandExecResult

if TYPE_CHECKING:
    from sandbox.occ.changeset import Change, ChangesetResult, CommitOptions


class SnapshotManifest(Protocol):
    """Snapshot manifest shape needed by command execution."""

    version: int
    layers: tuple[object, ...]


class WorkspaceSnapshotLease(Protocol):
    lease_id: str
    manifest_version: int
    manifest: SnapshotManifest
    lowerdir: str
    timings: Mapping[str, float]


class WorkspaceLeaseClient(Protocol):
    """Layer-stack lease/snapshot client used by command execution."""

    def prepare_workspace_snapshot(
        self,
        *,
        workspace_ref: str,
        request_id: str,
    ) -> WorkspaceSnapshotLease: ...

    def release_lease(self, *, workspace_ref: str, lease_id: str) -> bool: ...


class OCCMutationClient(Protocol):
    """OCC mutation client used for shell-capture submission."""

    async def apply_changeset(
        self,
        typed_changes: Sequence[Change],
        *,
        snapshot: SnapshotManifest | None = None,
        options: CommitOptions | None = None,
        workspace_ref: str | None = None,
    ) -> ChangesetResult: ...


class CommandExecutor(Protocol):
    """Runnable command-exec boundary exposed to daemon/API adapters."""

    async def run(self, request: CommandExecRequest) -> CommandExecResult: ...


__all__ = [
    "CommandExecutor",
    "OCCMutationClient",
    "SnapshotManifest",
    "WorkspaceLeaseClient",
    "WorkspaceSnapshotLease",
]
