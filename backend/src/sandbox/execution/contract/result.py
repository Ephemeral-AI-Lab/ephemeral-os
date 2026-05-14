"""Result values for guarded command execution."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sandbox.occ.changeset import ChangesetResult
    from sandbox.execution.overlay.change import OverlayPathChange


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


__all__ = [
    "CommandExecResult",
    "MountMode",
    "ShellProcessResult",
    "WorkspaceCapture",
]
