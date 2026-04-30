"""Tool-side adapter for sandbox commit helpers.

Tools resolve attribution and the CI service from a
:class:`ToolExecutionContextService`, then call into :mod:`sandbox.commit`.
This module hosts the context-aware shims so that ``sandbox.commit`` itself
stays free of ``tools.*`` imports.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from sandbox.lifecycle.commit import (
    CommitOp,
    FileChangeResult,
    commit_metadata,
    failure_status,
    submit_commit,
    submit_shell_cmd,
)
from tools.core.ci_adapter import get_ci_service
from tools.core.ci_attribution import (
    agent_attribution_from_context,
    resolved_agent_id,
)
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
        agent_id=resolved_agent_id(context),
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
    attribution = agent_attribution_from_context(context)
    return await submit_shell_cmd(
        svc,
        resolved_sandbox,
        command=command,
        description=description,
        timeout=timeout,
        attribute_changes=attribute_changes,
        on_progress_line=on_progress_line,
        agent_id=attribution.agent_id,
        run_id=attribution.run_id,
        agent_run_id=attribution.agent_run_id,
        task_id=attribution.task_id,
    )
