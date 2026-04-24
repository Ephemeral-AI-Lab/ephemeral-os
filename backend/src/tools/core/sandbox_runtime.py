"""Shared execution-context helpers for sandbox-backed tools."""

from __future__ import annotations

import os
from typing import Any

from tools.core.base import ToolExecutionContext


def get_daytona_sandbox(context: ToolExecutionContext) -> Any | None:
    """Get the injected Daytona sandbox object, if available."""
    return context.metadata.get("daytona_sandbox")


def get_daytona_cwd(context: ToolExecutionContext) -> str:
    """Backward-compatible alias for the injected sandbox repo root."""
    return context.metadata.get("repo_root") or context.metadata.get("daytona_cwd") or ""


def resolve_daytona_path(path: str, context: ToolExecutionContext) -> str:
    """Resolve *path* against the injected Daytona cwd."""
    if not path:
        return get_daytona_cwd(context) or "."
    if path.startswith("/"):
        return path
    cwd = get_daytona_cwd(context)
    if not cwd:
        return path
    return os.path.normpath(f"{cwd}/{path}")


__all__ = [
    "get_daytona_cwd",
    "get_daytona_sandbox",
    "resolve_daytona_path",
]
