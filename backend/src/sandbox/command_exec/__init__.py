"""Facade for guarded command execution."""

from __future__ import annotations

from sandbox.command_exec.contract import (
    CommandExecutor,
    CommandExecRequest,
    CommandExecResult,
    MountMode,
    OCCMutationClient,
    SnapshotManifest,
    ShellProcessResult,
    WorkspaceCapture,
    WorkspaceLeaseClient,
    WorkspaceReplacementMountSpec,
    WorkspaceSnapshotLease,
)
from sandbox.command_exec.policy import (
    DEFAULT_COMMAND_EXEC_POLICY,
    CommandExecPolicy,
)

_LAZY_EXPORTS = {
    "capture_workspace_upperdir": (
        "sandbox.command_exec.workspace.capture",
        "capture_workspace_upperdir",
    ),
    "execute_command": ("sandbox.command_exec.executor", "execute_command"),
    "run_workspace_replaced_command": (
        "sandbox.command_exec.workspace.mount",
        "run_workspace_replaced_command",
    ),
}

__all__ = [
    "CommandExecRequest",
    "CommandExecResult",
    "CommandExecutor",
    "CommandExecPolicy",
    "MountMode",
    "OCCMutationClient",
    "SnapshotManifest",
    "ShellProcessResult",
    "WorkspaceCapture",
    "WorkspaceLeaseClient",
    "WorkspaceReplacementMountSpec",
    "WorkspaceSnapshotLease",
    "capture_workspace_upperdir",
    "DEFAULT_COMMAND_EXEC_POLICY",
    "execute_command",
    "run_workspace_replaced_command",
]


def __getattr__(name: str) -> object:
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module_name, attribute = target
    from importlib import import_module

    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value
