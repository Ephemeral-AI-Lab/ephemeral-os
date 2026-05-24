"""Read primitive for namespace-mounted workspaces."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from sandbox._shared.models import ReadFileResult
from sandbox._shared.tool_primitives.file_ops import open_no_follow

_MAX_READ_BYTES = 16 * 1024 * 1024


def compute(args: Mapping[str, object] | str) -> ReadFileResult:
    path = _path_from_args(args)
    try:
        fd = open_no_follow(path, os.O_RDONLY)
    except FileNotFoundError:
        return ReadFileResult(success=True, content="", exists=False)
    with os.fdopen(fd, "rb") as handle:
        size = os.fstat(handle.fileno()).st_size
        if size > _MAX_READ_BYTES:
            raise ValueError(f"file too large: {size} > {_MAX_READ_BYTES} bytes")
        data = handle.read()
    return ReadFileResult(content=data.decode("utf-8", "replace"))


def _path_from_args(args: Mapping[str, object] | str) -> str:
    raw = args.get("path") if isinstance(args, Mapping) else args
    path = str(raw or "").strip()
    if not path:
        raise ValueError("path is required")
    return _absolute_no_escape(path)


def _absolute_no_escape(path: str) -> str:
    candidate = Path(path)
    if not candidate.is_absolute():
        if ".." in candidate.parts:
            raise ValueError(f"path escapes workspace via '..': {path}")
        candidate = Path.cwd() / candidate
    return candidate.as_posix()


__all__ = ["compute"]
