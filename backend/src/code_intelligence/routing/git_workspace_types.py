"""Types shared by Git workspace CodeAct auditing components."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


GitWorkspaceFileStatus = Literal["add", "modify", "delete", "rename"]


class GitWorkspaceError(RuntimeError):
    """Base error for Git workspace auditing failures."""


class GitWorkspaceUnsupportedChangeError(GitWorkspaceError):
    """Raised when Git reports a change kind that OCC cannot represent."""


class GitWorkspacePrepareError(GitWorkspaceError):
    """Raised when a workspace slot cannot be prepared for a command."""


class GitWorkspaceCommandError(GitWorkspaceError):
    """Raised when the sandbox command transport fails."""


@dataclass(frozen=True)
class WorkspaceDiffFile:
    """One file entry in a Git workspace diff."""

    path: str
    old_path: str | None
    status: GitWorkspaceFileStatus
    base_existed: bool
    base_hash: str
    final_existed: bool
    final_hash: str
    base_content: str
    final_content: str | None


@dataclass(frozen=True)
class WorkspaceDiff:
    """The full diff produced by one leased Git workspace operation."""

    files: tuple[WorkspaceDiffFile, ...]
    baseline_commit: str
    workspace_root: str
    command_exit_code: int
    stdout: str
    patch: str = ""


@dataclass(frozen=True)
class GitWorkspaceCommandResult:
    """Result of running the user command inside a Git workspace slot."""

    stdout: str
    exit_code: int


@dataclass(frozen=True)
class GitWorkspaceLease:
    """A single leased Git workspace slot."""

    slot_id: str
    slot_path: str
    pooled: bool = True


__all__ = [
    "GitWorkspaceCommandError",
    "GitWorkspaceCommandResult",
    "GitWorkspaceError",
    "GitWorkspaceFileStatus",
    "GitWorkspaceLease",
    "GitWorkspacePrepareError",
    "GitWorkspaceUnsupportedChangeError",
    "WorkspaceDiff",
    "WorkspaceDiffFile",
]
