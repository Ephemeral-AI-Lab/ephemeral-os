"""Facade for guarded command execution."""

from __future__ import annotations

from sandbox.command_exec.contract import (
    CommandExecutor,
    CommandExecRequest,
    CommandExecResult,
    MountMode,
    OCCMutationClient,
    ShellProcessResult,
    WorkspaceCapture,
    WorkspaceLeaseClient,
    WorkspaceReplacementMountSpec,
    WorkspaceSnapshotLease,
)
from sandbox.command_exec.executor import execute_command
from sandbox.command_exec.workspace.capture import capture_workspace_upperdir
from sandbox.command_exec.workspace.mount import run_workspace_replaced_command

__all__ = [
    "CommandExecRequest",
    "CommandExecResult",
    "CommandExecutor",
    "MountMode",
    "OCCMutationClient",
    "ShellProcessResult",
    "WorkspaceCapture",
    "WorkspaceLeaseClient",
    "WorkspaceReplacementMountSpec",
    "WorkspaceSnapshotLease",
    "capture_workspace_upperdir",
    "execute_command",
    "run_workspace_replaced_command",
]
