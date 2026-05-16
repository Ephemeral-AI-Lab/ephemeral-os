"""Public sandbox API package and compatibility request/result exports.

Request and result dataclasses are owned by :mod:`sandbox.models`; they are
re-exported here to preserve the existing public import path.

Import ordering is load-bearing: ``sandbox.models`` must bind before
``sandbox.api._provider_control_plane`` runs, because the chain
``_provider_control_plane -> host.lifecycle -> plugin.session ->
tools.sandbox._lib.session`` re-enters this package looking for
``SandboxCaller``. Do not let an auto-formatter reorder these blocks.
"""

from __future__ import annotations

from sandbox._shared.models import (
    ConflictInfo,
    EditFileRequest,
    EditFileResult,
    GuardedResultBase,
    RawExecResult,
    ReadFileRequest,
    ReadFileResult,
    SandboxCaller,
    SandboxRequestBase,
    SandboxResultBase,
    SearchReplaceEdit,
    ShellRequest,
    ShellResult,
    WriteFileRequest,
    WriteFileResult,
)
from sandbox.api._provider_control_plane import (  # isort: skip -- models precede control plane
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
from sandbox.api._tool_verbs.edit import edit_file
from sandbox.api._tool_verbs.raw_exec import raw_exec
from sandbox.api._tool_verbs.read import read_file
from sandbox.api._tool_verbs.shell import shell
from sandbox.api._tool_verbs.write import write_file

__all__ = [
    "ConflictInfo",
    "EditFileRequest",
    "EditFileResult",
    "GuardedResultBase",
    "RawExecResult",
    "ReadFileRequest",
    "ReadFileResult",
    "SandboxCaller",
    "SandboxRequestBase",
    "SandboxResultBase",
    "SearchReplaceEdit",
    "ShellRequest",
    "ShellResult",
    "WriteFileRequest",
    "WriteFileResult",
    "configured_sandbox_defaults",
    "context_preparer_for",
    "create_sandbox",
    "delete_sandbox",
    "edit_file",
    "ensure_sandbox_running",
    "get_build_logs_url",
    "get_health",
    "get_sandbox",
    "get_signed_preview_url",
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
