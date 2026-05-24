"""Pure tool primitive implementations for namespace-mounted workspaces."""

from __future__ import annotations

from sandbox._shared.tool_primitives import edit, glob, grep, read, write

VERB_TABLE = {
    "read_file": read.compute,
    "write_file": write.compute,
    "edit_file": edit.compute,
    "grep": grep.compute,
    "glob": glob.compute,
}

__all__ = [
    "capture",
    "edit",
    "file_ops",
    "glob",
    "grep",
    "read",
    "shell",
    "write",
    "VERB_TABLE",
]
