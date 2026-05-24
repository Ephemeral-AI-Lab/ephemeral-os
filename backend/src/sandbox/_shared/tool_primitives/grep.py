"""Grep primitive for namespace-mounted workspaces."""

from __future__ import annotations

from sandbox._shared.models import GrepResult
from sandbox._shared.tool_primitives.file_ops import walk_dirs_no_follow


def compute(root: str, pattern: str, *, case_insensitive: bool = False) -> GrepResult:
    needle = pattern.lower() if case_insensitive else pattern
    matches: list[str] = []
    for path in walk_dirs_no_follow(root):
        text = path.read_bytes().decode("utf-8", "replace")
        haystack = text.lower() if case_insensitive else text
        if needle in haystack:
            matches.append(path.as_posix())
    filenames = tuple(sorted(matches))
    return GrepResult(filenames=filenames, num_files=len(filenames), num_matches=len(filenames))


__all__ = ["compute"]
