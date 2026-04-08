"""CI integration helpers for the Daytona toolkit.

Provides service acquisition, tree cache priming after writes,
and shell mutation detection. All CI features are optional —
tools degrade gracefully if no CI service is configured.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from tools.core.base import ToolExecutionContext

logger = logging.getLogger(__name__)


def get_ci_service(context: ToolExecutionContext) -> Any | None:
    """Get the CodeIntelligenceService from context, or None if unavailable."""
    return context.metadata.get("ci_service")


def get_daytona_sandbox(context: ToolExecutionContext) -> Any | None:
    """Get the injected Daytona sandbox object, if available."""
    return context.metadata.get("daytona_sandbox")


def get_daytona_cwd(context: ToolExecutionContext) -> str:
    """Get the injected Daytona working directory, if available."""
    return context.metadata.get("daytona_cwd") or ""


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


def prime_cache_after_write(context: ToolExecutionContext, file_path: str, content: str) -> None:
    """Prime the tree cache and refresh the symbol index after a write."""
    svc = get_ci_service(context)
    if svc is None:
        return
    try:
        svc.tree_cache.put_content(file_path, content)
        svc.symbol_index.refresh(file_path, content)
        svc.lsp_client.invalidate(file_path)
    except Exception:
        logger.debug("CI prime_cache_after_write failed for %s", file_path)


def record_edit_in_ledger(
    context: ToolExecutionContext,
    file_path: str,
    agent_id: str = "",
    edit_type: str = "edit",
    old_hash: str = "",
    new_hash: str = "",
    description: str = "",
) -> None:
    """Record an edit in the CI ledger if available."""
    svc = get_ci_service(context)
    if svc is None:
        return
    try:
        svc.ledger.record(
            file_path=file_path,
            agent_id=agent_id,
            edit_type=edit_type,
            old_hash=old_hash,
            new_hash=new_hash,
            description=description,
        )
    except Exception:
        logger.debug("CI record_edit_in_ledger failed for %s", file_path)

