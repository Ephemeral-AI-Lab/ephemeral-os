"""Client protocols consumed by guarded command execution."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from sandbox.command_exec.contract.request import CommandExecRequest
from sandbox.command_exec.contract.result import CommandExecResult
from sandbox.layer_stack.manifest import Manifest
from sandbox.occ import Change, ChangesetResult, CommitOptions


class WorkspaceSnapshotLease(Protocol):
    lease_id: str
    manifest_version: int
    manifest: Manifest
    lowerdir: str
    timings: dict[str, float]


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
        snapshot: Manifest | None = None,
        options: CommitOptions | None = None,
        workspace_ref: str | None = None,
    ) -> ChangesetResult: ...


class CommandExecutor(Protocol):
    """Runnable command-exec boundary exposed to daemon/API adapters."""

    async def run(self, request: CommandExecRequest) -> CommandExecResult: ...


__all__ = [
    "CommandExecutor",
    "OCCMutationClient",
    "WorkspaceLeaseClient",
    "WorkspaceSnapshotLease",
]
