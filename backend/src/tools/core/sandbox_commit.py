"""Tool-side context shim around :mod:`sandbox.api.audit`.

The provider-neutral commit/shell helpers live in
:mod:`sandbox.api.audit` after the Phase 1 relocation. This shim adds
the ``ToolExecutionContextService``-aware wrappers tools call so the
audit façade itself stays free of ``tools.*`` imports.

Step 10 of the migration plan deletes this module once
``tools/sandbox_toolkit`` replaces ``tools/daytona_toolkit`` and tools
reach the sandbox through ``context.sandbox_api``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from sandbox.api.audit import (
    CommitOp,
    FileChangeResult,
    commit_metadata,
    failure_status,
    submit_commit,
    submit_shell_cmd,
)
from tools.core.ci_adapter import get_ci_service
from tools.core.ci_attribution import actor_from_context
from tools.core.context import ToolExecutionContextService

if TYPE_CHECKING:
    from sandbox.code_intelligence.core.types import OperationResult


__all__ = [
    "CommitOp",
    "FileChangeResult",
    "commit_metadata",
    "failure_status",
    "submit_commit_from_context",
    "submit_shell_cmd_from_context",
]


async def submit_commit_from_context(
    context: ToolExecutionContextService,
    *,
    op: CommitOp,
    specs: Sequence[Any],
    fallback_paths: Sequence[str],
    description: str,
) -> FileChangeResult["OperationResult"]:
    """Submit a write/edit/delete/move commit, resolving args from *context*."""
    svc = get_ci_service(context)
    if svc is None:
        raise RuntimeError(
            "submit_commit_from_context requires an active ci_service; "
            "caller must short-circuit with ci_write_required_result first",
        )
    sandbox = context.get("ci_sandbox") or context.get("daytona_sandbox")
    return await submit_commit(
        svc,
        op=op,
        specs=specs,
        fallback_paths=fallback_paths,
        description=description,
        actor=actor_from_context(context),
        sandbox=sandbox,
    )


async def submit_shell_cmd_from_context(
    context: ToolExecutionContextService,
    *,
    command: str,
    description: str,
    timeout: int | None = None,
    sandbox: Any | None = None,
    attribute_changes: bool = True,
    on_progress_line: Callable[[str], None] | None = None,
) -> FileChangeResult[SimpleNamespace]:
    """Run a shell command through the CI service, resolving args from *context*."""
    svc = get_ci_service(context)
    if svc is None:
        raise RuntimeError(
            "submit_shell_cmd_from_context requires an active ci_service; "
            "caller must short-circuit before entering the façade",
        )
    resolved_sandbox = sandbox
    if resolved_sandbox is None:
        resolved_sandbox = context.get("ci_sandbox") or context.get("daytona_sandbox")
    if resolved_sandbox is None:
        raise RuntimeError(
            "submit_shell_cmd_from_context requires a sandbox in tool execution "
            "context (ci_sandbox or daytona_sandbox) or as an explicit argument",
        )
    return await submit_shell_cmd(
        svc,
        resolved_sandbox,
        command=command,
        description=description,
        timeout=timeout,
        attribute_changes=attribute_changes,
        on_progress_line=on_progress_line,
        actor=actor_from_context(context),
    )
