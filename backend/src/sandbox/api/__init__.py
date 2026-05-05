"""Public sandbox API package and request/result models."""

from __future__ import annotations

from sandbox.api.utils.models import (
    ConflictInfo,
    EditFileRequest,
    EditFileResult,
    GuardedResultBase,
    RawExecResult,
    ReadFileRequest,
    ReadFileResult,
    SandboxCaller,
    SandboxResultBase,
    SearchReplaceEdit,
    ShellRequest,
    ShellResult,
    WriteFileRequest,
    WriteFileResult,
)
from sandbox.api.facade import SandboxAPI, api


def __getattr__(name: str) -> object:
    if name == "edit_file":
        from sandbox.api.tool.edit import edit_file

        return edit_file
    if name == "raw_exec":
        from sandbox.api.tool.raw_exec import raw_exec

        return raw_exec
    if name == "read_file":
        from sandbox.api.tool.read import read_file

        return read_file
    if name == "shell":
        from sandbox.api.tool.shell import shell

        return shell
    if name == "write_file":
        from sandbox.api.tool.write import write_file

        return write_file
    if name == "status":
        import importlib

        return importlib.import_module("sandbox.api.status")
    raise AttributeError(name)

__all__ = [
    "ConflictInfo",
    "EditFileRequest",
    "EditFileResult",
    "GuardedResultBase",
    "RawExecResult",
    "ReadFileRequest",
    "ReadFileResult",
    "SandboxCaller",
    "SandboxResultBase",
    "SearchReplaceEdit",
    "SandboxAPI",
    "ShellRequest",
    "ShellResult",
    "WriteFileRequest",
    "WriteFileResult",
    "api",
    "edit_file",
    "raw_exec",
    "read_file",
    "shell",
    "status",
    "write_file",
]
