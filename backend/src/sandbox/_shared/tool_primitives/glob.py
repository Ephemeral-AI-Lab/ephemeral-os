"""Glob primitive for namespace-mounted workspaces."""

from __future__ import annotations

import fnmatch
from collections.abc import Mapping
from pathlib import Path, PurePosixPath

from sandbox._shared.models import GlobResult
from sandbox._shared.tool_primitives.file_ops import (
    is_regular_file_no_follow,
    walk_dirs_no_follow,
)

DEFAULT_GLOB_LIMIT = 100


def compute(args: Mapping[str, object] | str) -> GlobResult:
    if isinstance(args, Mapping):
        pattern = str(args.get("pattern") or "").strip()
        root = _absolute_no_escape(str(args.get("path") or "."))
    else:
        pattern = str(args or "").strip()
        root = Path.cwd().as_posix()
    if not pattern:
        raise ValueError("pattern is required")
    matches = [
        _display_path(path)
        for path in walk_dirs_no_follow(root)
        if _matches(root, path, pattern) and is_regular_file_no_follow(path)
    ]
    filenames = tuple(sorted(matches)[:DEFAULT_GLOB_LIMIT])
    return GlobResult(
        filenames=filenames,
        num_files=len(filenames),
        truncated=len(matches) > DEFAULT_GLOB_LIMIT,
    )


def _matches(root: str, path: Path, pattern: str) -> bool:
    text = path.as_posix()
    if "/.git/" in text:
        return False
    try:
        rel = path.resolve(strict=False).relative_to(
            Path(root).resolve(strict=False)
        ).as_posix()
    except ValueError:
        return False
    if "/" not in pattern:
        return "/" not in rel and fnmatch.fnmatch(rel, pattern)
    patterns = {pattern}
    if "**/" in pattern:
        patterns.add(pattern.replace("**/", ""))
    candidate = PurePosixPath(rel)
    return any(candidate.match(option) for option in patterns)


def _display_path(path: Path) -> str:
    cwd = Path.cwd().resolve(strict=False)
    try:
        return path.resolve(strict=False).relative_to(cwd).as_posix()
    except ValueError:
        return path.as_posix()


def _absolute_no_escape(path: str) -> str:
    candidate = Path(str(path or "."))
    if not candidate.is_absolute():
        if ".." in candidate.parts:
            raise ValueError(f"path escapes workspace via '..': {path}")
        candidate = Path.cwd() / candidate
    return candidate.as_posix()


__all__ = ["compute"]
