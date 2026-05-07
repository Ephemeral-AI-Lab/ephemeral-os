"""Result values for guarded command execution."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field


@dataclass(frozen=True)
class WorkspaceCapture:
    """Workspace-relative changes captured from one command upperdir."""

    changes: Sequence[object]
    snapshot_version: int
    mount_mode: str


@dataclass(frozen=True)
class CommandExecResult:
    """Final command-exec response before public API projection."""

    exit_code: int
    stdout: str
    stderr: str
    workspace_capture: WorkspaceCapture
    occ_result: object
    timings: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class ShellProcessResult:
    """Raw process result and capture locations."""

    exit_code: int
    stdout_ref: str
    stderr_ref: str
    mounted_workspace_root: str
    mount_mode: str


__all__ = [
    "CommandExecResult",
    "ShellProcessResult",
    "WorkspaceCapture",
]
