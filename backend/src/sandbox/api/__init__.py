"""Public sandbox API package: request/result dataclasses and verb dispatchers.

Request and result dataclasses are owned by :mod:`sandbox._shared.models` and
re-exported here to preserve the existing public import path.

Import ordering is load-bearing: ``sandbox._shared.models`` must bind before
``sandbox.api._sandbox_control`` runs, because the chain
``_sandbox_control -> host.lifecycle -> plugin.session ->
tools.sandbox._lib.session`` re-enters this package looking for
``SandboxCaller``. Do not let an auto-formatter reorder these blocks.
"""

from __future__ import annotations

from sandbox._shared.models import (
    ConflictInfo,
    EditFileRequest,
    EditFileResult,
    EnterIsolatedWorkspaceRequest,
    EnterIsolatedWorkspaceResult,
    ExitIsolatedWorkspaceRequest,
    ExitIsolatedWorkspaceResult,
    GlobRequest,
    GlobResult,
    GrepRequest,
    GrepResult,
    GuardedResultBase,
    Intent,
    LifecycleError,
    LifecycleResultBase,
    RawExecResult,
    ReadFileRequest,
    ReadFileResult,
    SandboxCaller,
    SandboxRequestBase,
    SandboxResultBase,
    SearchReplaceEdit,
    ShellRequest,
    ShellResult,
    ToolCallRequest,
    ToolCallResult,
    WriteFileRequest,
    WriteFileResult,
)
from sandbox.api._sandbox_control import (  # isort: skip -- models precede sandbox control
    configured_sandbox_defaults,
    context_preparer_for,
    create_sandbox,
    delete_sandbox,
    ensure_sandbox_running,
    get_build_logs_url,
    get_health,
    get_sandbox,
    get_signed_preview_url,
    list_sandboxes,
    list_snapshots,
    set_sandbox_labels,
    start_sandbox,
    stop_sandbox,
)
from sandbox.api.tool.edit import edit_file
from sandbox.api.tool.glob import glob
from sandbox.api.tool.grep import grep
from sandbox.api.tool.read import read_file
from sandbox.api.tool.shell import shell
from sandbox.api.tool.write import write_file
from sandbox.api._raw_exec import raw_exec
from sandbox.api.request_lifecycle import cancel, heartbeat, inflight_count

__all__ = [
    "ConflictInfo",
    "EditFileRequest",
    "EditFileResult",
    "EnterIsolatedWorkspaceRequest",
    "EnterIsolatedWorkspaceResult",
    "ExitIsolatedWorkspaceRequest",
    "ExitIsolatedWorkspaceResult",
    "GlobRequest",
    "GlobResult",
    "GrepRequest",
    "GrepResult",
    "GuardedResultBase",
    "Intent",
    "LifecycleError",
    "LifecycleResultBase",
    "RawExecResult",
    "ReadFileRequest",
    "ReadFileResult",
    "SandboxCaller",
    "SandboxRequestBase",
    "SandboxResultBase",
    "SearchReplaceEdit",
    "ShellRequest",
    "ShellResult",
    "ToolCallRequest",
    "ToolCallResult",
    "WriteFileRequest",
    "WriteFileResult",
    "configured_sandbox_defaults",
    "context_preparer_for",
    "cancel",
    "create_sandbox",
    "delete_sandbox",
    "edit_file",
    "ensure_sandbox_running",
    "get_build_logs_url",
    "get_health",
    "get_sandbox",
    "get_signed_preview_url",
    "heartbeat",
    "glob",
    "grep",
    "inflight_count",
    "list_sandboxes",
    "list_snapshots",
    "raw_exec",
    "read_file",
    "set_sandbox_labels",
    "shell",
    "start_sandbox",
    "stop_sandbox",
    "write_file",
]
