"""Sandbox workspace discovery — resolve cwd and inject code intelligence services."""

from __future__ import annotations

import inspect
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _sandbox_exec_is_async(sandbox: Any) -> bool:
    """Best-effort detection for async Daytona sandbox wrappers.

    Async sandbox warmup must stay lazy. Eager CI/LSP warmup against an async
    sandbox can corrupt the shared aiohttp client across loop boundaries,
    which then breaks later ``daytona_*`` tool calls with
    ``RuntimeError('Event loop is closed')``.
    """
    process = getattr(sandbox, "process", None)
    exec_fn = getattr(process, "exec", None)
    return bool(exec_fn) and inspect.iscoroutinefunction(exec_fn)


def _ci_sandbox_handle(sandbox_id: str | None, sandbox: Any) -> Any:
    """Return a sandbox handle safe for CI warmup.

    Worker tools use an async sandbox so shell/file operations can be awaited
    and cancelled. CI warmup should not reuse that same async handle because
    some CI/LSP warmup paths are synchronous and may survive across loop
    boundaries. When we detect an async Daytona sandbox, resolve a separate
    sync handle for CI instead.
    """
    if not sandbox_id or sandbox is None or not _sandbox_exec_is_async(sandbox):
        return sandbox
    try:
        from sandbox.service import SandboxService

        return SandboxService().get_sandbox_object(sandbox_id)
    except Exception:
        logger.debug(
            "Could not resolve sync sandbox handle for CI warmup on %s; falling back to async handle",
            sandbox_id,
            exc_info=True,
        )
        return sandbox


def discover_workspace(sandbox: Any) -> str | None:
    project_dir = getattr(sandbox, "project_dir", None)
    if project_dir:
        return project_dir
    try:
        resp = sandbox.process.exec("pwd")
        if resp.exit_code == 0 and resp.result:
            return resp.result.strip()
    except Exception:
        pass
    return None


async def discover_workspace_async(sandbox: Any) -> str | None:
    project_dir = getattr(sandbox, "project_dir", None)
    if project_dir:
        return project_dir
    try:
        resp = await sandbox.process.exec("pwd")
        if resp.exit_code == 0 and resp.result:
            return resp.result.strip()
    except Exception:
        pass
    return None


def inject_code_intelligence(
    context: Any,
    sandbox_id: str | None,
    sandbox: Any,
    workspace_root: str,
) -> None:
    if sandbox_id and "ci_service" not in context.metadata:
        try:
            from code_intelligence.routing.service import get_code_intelligence

            ci_sandbox = _ci_sandbox_handle(sandbox_id, sandbox)
            svc = get_code_intelligence(
                sandbox_id=sandbox_id,
                workspace_root=workspace_root,
                sandbox=ci_sandbox,
            )
            try:
                if Path(workspace_root).is_dir():
                    svc.ensure_initialized(wait=False)
                else:
                    svc.lsp_client.ensure_ready()
            except Exception:
                logger.debug(
                    "CI service warmup skipped for sandbox %s", sandbox_id, exc_info=True
                )
            context.metadata["ci_service"] = svc
        except Exception:
            logger.debug("CI service not available for sandbox %s", sandbox_id)
