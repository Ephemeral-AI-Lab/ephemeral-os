"""Public command-exec contract values: request, result, ports, spec.

Collapsed from execution/contract/{request,result,ports,spec}.py per the
sandbox-reframe RFC §4 Wave 5c. Behavior is preserved verbatim.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from sandbox.execution.overlay.change import OverlayPathChange
    from sandbox.occ.changeset import Change, ChangesetResult, CommitOptions


# ---- request ---------------------------------------------------------------


@dataclass(frozen=True)
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
        timeout = self.timeout_seconds
        if timeout is not None and timeout <= 0:
            raise ValueError("timeout_seconds must be positive when provided")

        cwd_raw = str(self.cwd).strip() or "."
        cwd_normalized = os.path.normpath(cwd_raw)
        if cwd_normalized == ".." or cwd_normalized.startswith("../"):
            raise ValueError(f"cwd must not escape workspace root: {cwd_raw!r}")
        if not cwd_normalized.startswith("/") and ".." in cwd_normalized.split("/"):
            raise ValueError(f"cwd must not contain '..' segments: {cwd_raw!r}")

        object.__setattr__(self, "request_id", request_id)
        object.__setattr__(self, "workspace_ref", workspace_ref)
        object.__setattr__(self, "workspace_root", workspace_root.rstrip("/") or "/")
        object.__setattr__(self, "command", command)
        object.__setattr__(self, "cwd", cwd_normalized)
        object.__setattr__(
            self,
            "env",
            {str(key): str(value) for key, value in self.env.items()},
        )
        object.__setattr__(self, "actor_id", str(self.actor_id))
        object.__setattr__(self, "description", str(self.description or "shell"))


# ---- result ----------------------------------------------------------------


class MountMode(str, Enum):
    """Workspace replacement mode used for one command."""

    COPY_BACKED = "copy_backed"
    PRIVATE_NAMESPACE = "private_namespace"


@dataclass(frozen=True)
class WorkspaceCapture:
    """Workspace-relative changes captured from one command upperdir."""

    changes: Sequence[OverlayPathChange]
    snapshot_version: int
    mount_mode: MountMode

    def __post_init__(self) -> None:
        object.__setattr__(self, "mount_mode", MountMode(self.mount_mode))


@dataclass(frozen=True)
class CommandExecResult:
    """Final command-exec response before public API projection."""

    exit_code: int
    stdout: str
    stderr: str
    workspace_capture: WorkspaceCapture
    occ_result: ChangesetResult
    timings: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class ShellProcessResult:
    """Raw process result and capture locations."""

    exit_code: int
    stdout_ref: str
    stderr_ref: str
    mounted_workspace_root: str
    mount_mode: MountMode

    def __post_init__(self) -> None:
        object.__setattr__(self, "mount_mode", MountMode(self.mount_mode))


# ---- ports -----------------------------------------------------------------


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


# ---- spec ------------------------------------------------------------------


@dataclass(frozen=True)
class WorkspaceReplacementMountSpec:
    """Filesystem inputs for replacing the assigned workspace root."""

    workspace_root: str
    lowerdir: str
    upperdir: str
    workdir: str
    scratch_root: str

    def __post_init__(self) -> None:
        if not str(self.workspace_root).startswith("/"):
            raise ValueError("workspace_root must be absolute")
        if not str(self.scratch_root).strip():
            raise ValueError("scratch_root must not be empty")
        scratch_root = Path(self.scratch_root).resolve(strict=False)
        resolved_paths: dict[str, Path] = {}
        for field_name in ("lowerdir", "upperdir", "workdir"):
            if not str(getattr(self, field_name)).strip():
                raise ValueError(f"{field_name} must not be empty")
            path = Path(str(getattr(self, field_name))).resolve(strict=False)
            if path == scratch_root or not path.is_relative_to(scratch_root):
                raise ValueError(
                    f"{field_name} must be strictly under scratch_root: {path}"
                )
            resolved_paths[field_name] = path

        seen: dict[Path, str] = {}
        for field_name, path in resolved_paths.items():
            duplicate = seen.get(path)
            if duplicate is not None:
                raise ValueError(
                    f"{field_name} must be distinct from {duplicate}: {path}"
                )
            seen[path] = field_name


__all__ = [
    "CommandExecRequest",
    "CommandExecResult",
    "CommandExecutor",
    "MountMode",
    "OCCMutationClient",
    "ShellProcessResult",
    "SnapshotManifest",
    "WorkspaceCapture",
    "WorkspaceLeaseClient",
    "WorkspaceReplacementMountSpec",
    "WorkspaceSnapshotLease",
]
