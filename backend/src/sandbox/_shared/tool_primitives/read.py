"""Read primitive for namespace-mounted workspaces."""

from __future__ import annotations

from sandbox._shared.models import ReadFileResult
from sandbox._shared.tool_primitives.file_ops import read_bytes_no_follow


def compute(path: str) -> ReadFileResult:
    try:
        data = read_bytes_no_follow(path)
    except FileNotFoundError:
        return ReadFileResult(success=False, content="", exists=False)
    return ReadFileResult(content=data.decode("utf-8", "replace"))


__all__ = ["compute"]
