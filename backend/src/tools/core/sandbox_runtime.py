"""Shared execution-context helpers for sandbox-backed tools."""

from __future__ import annotations

import logging
import os
from typing import Any

from tools.core.context import ToolExecutionContextService

logger = logging.getLogger(__name__)


def get_daytona_sandbox(context: ToolExecutionContextService) -> Any | None:
    """Get the injected Daytona sandbox object, if available."""
    return context.daytona_sandbox


def _sandbox_repo_root(context: ToolExecutionContextService) -> str:
    return context.repo_root or ""


def resolve_daytona_path(path: str, context: ToolExecutionContextService) -> str:
    """Resolve *path* against the injected sandbox repo root."""
    if not path:
        return _sandbox_repo_root(context) or "."
    if path.startswith("/"):
        return path
    cwd = _sandbox_repo_root(context)
    if not cwd:
        return path
    return os.path.normpath(f"{cwd}/{path}")


async def resolve_sandbox(context: ToolExecutionContextService) -> Any | None:
    """Return the bound Daytona sandbox, lazily attaching from ``sandbox_id``."""
    sandbox = get_daytona_sandbox(context)
    if sandbox is not None:
        return sandbox
    sandbox_id = str(context.get("sandbox_id") or "").strip()
    if not sandbox_id:
        return None
    try:
        from sandbox.async_client import get_async_sandbox

        sandbox = await get_async_sandbox(sandbox_id)
        context["daytona_sandbox"] = sandbox
        return sandbox
    except Exception:
        logger.debug("Lazy sandbox attach failed for %s", sandbox_id, exc_info=True)
        return None


__all__ = [
    "get_daytona_sandbox",
    "resolve_daytona_path",
    "resolve_sandbox",
]
