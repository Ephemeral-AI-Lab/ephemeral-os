"""Write primitive for namespace-mounted workspaces."""

from __future__ import annotations

from sandbox._shared.models import WriteFileResult
from sandbox._shared.tool_primitives.file_ops import write_bytes_no_follow


def compute(path: str, content: str, *, overwrite: bool = True) -> WriteFileResult:
    write_bytes_no_follow(path, content.encode("utf-8"), overwrite=overwrite)
    return WriteFileResult(changed_paths=(path,), status="ok")


__all__ = ["compute"]
