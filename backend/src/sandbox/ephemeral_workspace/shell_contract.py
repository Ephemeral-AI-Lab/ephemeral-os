"""Shell pipeline contract values: request, result, and ports."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from sandbox.layer_stack.manifest import Manifest
    from sandbox.overlay.path_change import OverlayPathChange
    from sandbox.occ.changeset import Change, CommitOptions, FileResult

PRIVATE_NAMESPACE_MOUNT = "private_namespace"


# ---- request ---------------------------------------------------------------


@dataclass
class CommandExecRequest:
    """One shell command against a workspace replacement mount."""

    request_id: str
    workspace_ref: str
    workspace_root: str
    command: tuple[str, ...]
    cwd: str = "."
    env: Mapping[str, str] = field(default_factory=dict)
    timeout_seconds: float | None = None
    actor_id: str = ""
    description: str = "shell"

    def __post_init__(self) -> None:
        request_id = str(self.request_id).strip()
        if not request_id:
            raise ValueError("request_id must not be empty")
        workspace_ref = str(self.workspace_ref).strip()
        if not workspace_ref:
            raise ValueError("workspace_ref must not be empty")
        workspace_root = str(self.workspace_root).strip()
        if not workspace_root.startswith("/"):
            raise ValueError("workspace_root must be an absolute path")
        command = tuple(str(part) for part in self.command)
        if not command or any(part == "" for part in command):
            raise ValueError("command must contain non-empty argv parts")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive when provided")

        cwd_raw = str(self.cwd).strip() or "."
        cwd_normalized = os.path.normpath(cwd_raw)
        if cwd_normalized == ".." or cwd_normalized.startswith("../"):
            raise ValueError(f"cwd must not escape workspace root: {cwd_raw!r}")
        if not cwd_normalized.startswith("/") and ".." in cwd_normalized.split("/"):
            raise ValueError(f"cwd must not contain '..' segments: {cwd_raw!r}")

        self.request_id = request_id
        self.workspace_ref = workspace_ref
        self.workspace_root = workspace_root.rstrip("/") or "/"
        self.command = command
        self.cwd = cwd_normalized
        self.env = {str(key): str(value) for key, value in self.env.items()}
        self.actor_id = str(self.actor_id)
        self.description = str(self.description or "shell")


@dataclass
class OverlayShellRequest:
    """One per-call shell request against a leased layer-stack snapshot."""

    request_id: str
    command: tuple[str, ...]
    cwd: str
    env: Mapping[str, str]
    timeout_seconds: float | None

    def __post_init__(self) -> None:
        request_id = str(self.request_id).strip()
        if not request_id:
            raise ValueError("request_id must not be empty")
        command = tuple(str(part) for part in self.command)
        if not command or any(part == "" for part in command):
            raise ValueError("command must contain non-empty argv parts")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive when provided")
        self.request_id = request_id
        self.command = command
        self.cwd = str(self.cwd).strip() or "."
        self.env = {str(key): str(value) for key, value in self.env.items()}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> OverlayShellRequest:
        command_raw = payload.get("command")
        if not isinstance(command_raw, list):
            raise ValueError("OverlayShellRequest.command must be a list")
        env_raw = payload.get("env") or {}
        if not isinstance(env_raw, Mapping):
            raise ValueError("OverlayShellRequest.env must be an object")
        timeout_raw = payload.get("timeout_seconds")
        return cls(
            request_id=str(payload.get("request_id") or ""),
            command=tuple(str(part) for part in command_raw),
            cwd=str(payload.get("cwd") or "."),
            env={str(key): str(value) for key, value in env_raw.items()},
            timeout_seconds=float(timeout_raw) if timeout_raw is not None else None,
        )


# ---- result ----------------------------------------------------------------


@dataclass
class WorkspaceCapture:
    """Workspace-relative changes captured from one command upperdir."""

    changes: Sequence[OverlayPathChange]
    snapshot_version: int
    mount_mode: str
    snapshot_manifest: SnapshotManifest | None = None

    def __post_init__(self) -> None:
        self.mount_mode = str(self.mount_mode)


@dataclass
class OverlayCapture:
    """Policy-blind shell execution result captured from a snapshot overlay."""

    exit_code: int
    stdout_ref: str
    stderr_ref: str
    snapshot_version: int
    changes: tuple[OverlayPathChange, ...]
    mount_mode: str = PRIVATE_NAMESPACE_MOUNT
    snapshot_manifest: Manifest | None = None
    timings: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.exit_code = int(self.exit_code)
        self.snapshot_version = int(self.snapshot_version)
        self.changes = tuple(self.changes)
        self.mount_mode = str(self.mount_mode)
        self.timings = MappingProxyType(
            {str(key): float(value) for key, value in self.timings.items()}
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "exit_code": self.exit_code,
            "stdout_ref": self.stdout_ref,
            "stderr_ref": self.stderr_ref,
            "snapshot_version": self.snapshot_version,
            "mount_mode": self.mount_mode,
            "changes": [change.to_dict() for change in self.changes],
            "snapshot_manifest": (
                self.snapshot_manifest.to_dict()
                if self.snapshot_manifest is not None
                else None
            ),
            "timings": dict(self.timings),
        }


@dataclass
class CommandExecResult:
    """Final command-exec response before public API projection."""

    exit_code: int
    stdout: str
    stderr: str
    stdout_ref: str
    stderr_ref: str
    workspace_capture: WorkspaceCapture
    occ_result: ChangesetResultLike
    timings: dict[str, float] = field(default_factory=dict)


@dataclass
class ShellProcessResult:
    """Raw process result and capture locations."""

    exit_code: int
    stdout_ref: str
    stderr_ref: str
    mounted_workspace_root: str
    mount_mode: str

    def __post_init__(self) -> None:
        self.mount_mode = str(self.mount_mode)


# ---- ports -----------------------------------------------------------------


class SnapshotManifest(Protocol):
    """Snapshot manifest shape needed by command execution."""

    version: int
    layers: tuple[object, ...]


class WorkspaceSnapshotLease(Protocol):
    lease_id: str
    manifest_version: int
    manifest: SnapshotManifest
    lowerdir: str | None
    layer_paths: tuple[str, ...] | None
    timings: Mapping[str, float]


class WorkspaceLeaseClient(Protocol):
    """Layer-stack lease/snapshot client used by command execution."""

    storage_root: Path

    def prepare_workspace_snapshot(
        self,
        *,
        request_id: str,
    ) -> WorkspaceSnapshotLease: ...

    def release_lease(self, *, lease_id: str) -> bool: ...


class OCCMutationClient(Protocol):
    """OCC mutation client used for shell-capture submission."""

    async def apply_changeset(
        self,
        typed_changes: Sequence[Change],
        *,
        snapshot: SnapshotManifest | None = None,
        options: CommitOptions | None = None,
        workspace_ref: str | None = None,
        run_maintenance: bool = True,
    ) -> ChangesetResultLike: ...

    async def run_maintenance_after_publish(
        self,
        result: ChangesetResultLike,
        *,
        workspace_ref: str | None = None,
    ) -> dict[str, float]: ...


class ChangesetResultLike(Protocol):
    """Minimal committed changeset result shape consumed by command execution."""

    files: Sequence[FileResult]
    timings: Mapping[str, float]
    published_manifest_version: int | None

    @property
    def success(self) -> bool: ...


@dataclass(frozen=True)
class EmptyChangesetResult:
    """No-op result for capture-only command execution paths."""

    files: tuple[object, ...] = ()
    timings: dict[str, float] = field(default_factory=dict)
    published_manifest_version: int | None = None

    @property
    def success(self) -> bool:
        return True


@dataclass(frozen=True)
class WorkspaceCapturePublishResult:
    """Result returned by the daemon-owned overlay publish facade."""

    path_changes: Sequence[OverlayPathChange]
    changeset: ChangesetResultLike
    timings: Mapping[str, float] = field(default_factory=dict)


class WorkspaceCapturePublisher(Protocol):
    """Daemon-owned publish boundary for command upperdir captures."""

    async def publish_cycle(
        self,
        *,
        request: CommandExecRequest,
        upperdir: str | Path,
        snapshot: SnapshotManifest,
        run_maintenance: bool = True,
    ) -> WorkspaceCapturePublishResult: ...

    async def run_maintenance_after_publish(
        self,
        result: ChangesetResultLike,
        *,
        workspace_ref: str | None = None,
    ) -> dict[str, float]: ...


__all__ = [
    "ChangesetResultLike",
    "CommandExecRequest",
    "CommandExecResult",
    "EmptyChangesetResult",
    "OCCMutationClient",
    "OverlayCapture",
    "OverlayShellRequest",
    "PRIVATE_NAMESPACE_MOUNT",
    "ShellProcessResult",
    "SnapshotManifest",
    "WorkspaceCapture",
    "WorkspaceCapturePublisher",
    "WorkspaceCapturePublishResult",
    "WorkspaceLeaseClient",
    "WorkspaceSnapshotLease",
]
