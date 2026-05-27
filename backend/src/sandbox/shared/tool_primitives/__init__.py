"""Pure tool primitive implementations for namespace-mounted workspaces."""

from __future__ import annotations

from sandbox.shared.tool_primitives import (
    edit,
    glob,
    grep,
    read,
    shell,
    workspace_filesystem,
    write,
)

VERB_TABLE = {
    "read_file": read.read_file,
    "write_file": write.write_file,
    "edit_file": edit.edit_file,
    "grep": grep.grep_files,
    "glob": glob.glob_files,
}

__all__ = [
    "edit",
    "glob",
    "grep",
    "read",
    "shell",
    "workspace_filesystem",
    "write",
    "VERB_TABLE",
]
