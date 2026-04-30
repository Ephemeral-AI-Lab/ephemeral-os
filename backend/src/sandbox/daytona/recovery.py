"""Sandbox attach + recovery for tools that lose their container handle."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from sandbox.daytona import _SandboxContext

logger = logging.getLogger(__name__)

_SANDBOX_RECOVERY_KEY = "daytona_recovery_attempts"
_SANDBOX_RECOVERY_PATTERNS = (
    "no such container",
    "container not found",
    "sandbox container not found",
)


def _sandbox_context_error(detail: str | None = None) -> str:
    base = (
        "No sandbox in context. "
        "Ensure tool context was initialized with a valid sandbox_id."
    )
    if detail:
        return f"{base} Last recovery error: {detail}"
    return base


def _is_recoverable_sandbox_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(pattern in text for pattern in _SANDBOX_RECOVERY_PATTERNS)


async def _attach_sandbox_to_context(context: _SandboxContext) -> Any:
    """Lazily attach sandbox + CI when prepare_context did not complete."""
    sandbox_id = str(context.get("sandbox_id") or "").strip()
    if not sandbox_id:
        raise RuntimeError(_sandbox_context_error())
    try:
        from sandbox.client.async_ import get_async_sandbox
        from sandbox.lifecycle.workspace import (
            discover_workspace_async,
            ensure_code_intelligence_runtime,
        )

        sandbox = await get_async_sandbox(sandbox_id)
        repo_root = context.get("repo_root")
        if not repo_root:
            project_dir = getattr(sandbox, "project_dir", None)
            repo_root = project_dir or await discover_workspace_async(sandbox)
        ensure_code_intelligence_runtime(
            context,
            sandbox_id=sandbox_id,
            sandbox=sandbox,
            workspace_root=repo_root,
        )
        return sandbox
    except Exception as exc:
        raise RuntimeError(_sandbox_context_error(str(exc))) from exc


async def _require_sandbox(context: _SandboxContext) -> Any:
    sandbox = context.get("daytona_sandbox")
    if sandbox is not None:
        return sandbox
    return await _attach_sandbox_to_context(context)


async def _recover_sandbox(context: _SandboxContext, exc: Exception) -> Any:
    """Restart/rebind the sandbox once after container-loss style failures."""
    if not _is_recoverable_sandbox_error(exc):
        raise exc
    attempts_value = context.get(_SANDBOX_RECOVERY_KEY, 0)
    try:
        attempts = int(attempts_value)
    except (TypeError, ValueError):
        attempts = 0
    if attempts >= 1:
        raise exc
    sandbox_id = str(context.get("sandbox_id") or "").strip()
    if not sandbox_id:
        raise exc
    context[_SANDBOX_RECOVERY_KEY] = attempts + 1
    logger.warning(
        "Recovering sandbox %s after tool failure: %s",
        sandbox_id,
        exc,
    )
    try:
        from sandbox.lifecycle.service import SandboxService

        await asyncio.to_thread(SandboxService().ensure_sandbox_running, sandbox_id)
    finally:
        context["daytona_sandbox"] = None
        context["ci_service"] = None
    recovered = await _attach_sandbox_to_context(context)
    logger.warning("Recovered sandbox %s and retrying tool once", sandbox_id)
    return recovered


async def _run_with_recovery(
    context: _SandboxContext,
    operation: Callable[[Any], Awaitable[Any]],
) -> Any:
    """Run a sandbox operation once, then retry after sandbox recovery."""
    sandbox = await _require_sandbox(context)
    try:
        return await operation(sandbox)
    except Exception as exc:
        return await operation(await _recover_sandbox(context, exc))


__all__ = [
    "_SANDBOX_RECOVERY_KEY",
    "_SANDBOX_RECOVERY_PATTERNS",
    "_attach_sandbox_to_context",
    "_is_recoverable_sandbox_error",
    "_recover_sandbox",
    "_require_sandbox",
    "_run_with_recovery",
    "_sandbox_context_error",
]
