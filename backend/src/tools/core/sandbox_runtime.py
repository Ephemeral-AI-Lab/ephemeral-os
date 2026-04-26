"""Shared execution-context helpers for sandbox-backed tools."""

from __future__ import annotations

import os
from typing import Any

from tools.core.base import ToolExecutionContext


def get_daytona_sandbox(context: ToolExecutionContext) -> Any | None:
    """Get the injected Daytona sandbox object, if available."""
    return context.metadata.get("daytona_sandbox")


def _sandbox_repo_root(context: ToolExecutionContext) -> str:
    return context.metadata.get("repo_root") or ""


def resolve_daytona_path(path: str, context: ToolExecutionContext) -> str:
    """Resolve *path* against the injected sandbox repo root."""
    if not path:
        return _sandbox_repo_root(context) or "."
    if path.startswith("/"):
        return path
    cwd = _sandbox_repo_root(context)
    if not cwd:
        return path
    return os.path.normpath(f"{cwd}/{path}")


__all__ = [
    "get_daytona_sandbox",
    "resolve_daytona_path",
]
