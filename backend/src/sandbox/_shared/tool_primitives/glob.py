"""Glob primitive for namespace-mounted workspaces."""

from __future__ import annotations

import glob as globlib

from sandbox._shared.models import GlobResult


def compute(pattern: str) -> GlobResult:
    filenames = tuple(sorted(globlib.glob(pattern, recursive=True)))
    return GlobResult(filenames=filenames, num_files=len(filenames))


__all__ = ["compute"]
