"""Write primitive for namespace-mounted workspaces."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from sandbox._shared.models import WriteFileResult
from sandbox._shared.tool_primitives.file_ops import write_bytes_no_follow


def compute(
    args: Mapping[str, object] | str,
    content: str | None = None,
    *,
    overwrite: bool = True,
) -> WriteFileResult:
    if isinstance(args, Mapping):
        path = _absolute_no_escape(str(args.get("path") or ""))
        content = str(args.get("content") or "")
        overwrite = bool(args.get("overwrite", overwrite))
    else:
        path = _absolute_no_escape(args)
        content = "" if content is None else content
    write_bytes_no_follow(path, str(content).encode("utf-8"), overwrite=overwrite)
    return WriteFileResult(changed_paths=(path,), status="ok")


def _absolute_no_escape(path: str) -> str:
    path = str(path or "").strip()
    if not path:
        raise ValueError("path is required")
    candidate = Path(path)
    if not candidate.is_absolute():
        if ".." in candidate.parts:
            raise ValueError(f"path escapes workspace via '..': {path}")
        candidate = Path.cwd() / candidate
    return candidate.as_posix()


__all__ = ["compute"]
