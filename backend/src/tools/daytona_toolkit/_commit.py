"""Unified entry point for OCC-gated tool calls.

Every Daytona tool that commits file changes (``daytona_write_file``,
``daytona_edit_file``, ``daytona_delete_file``, ``daytona_move_file``,
``daytona_codeact``) funnels through one of two façades here:

* :func:`submit_commit` wraps the sync ``svc.{write,edit,delete,move}_file``
  path (``OperationResult``).
* :func:`submit_codeact_cmd` wraps the async ``svc.cmd`` overlay-audit path
  (auditor ``SimpleNamespace``).

Both return :class:`FileChangeResult` so downstream post-hooks can read one
uniform shape regardless of which service call produced it. Tool-specific
payload fields (e.g. ``aborted_version`` status, per-spec errors, stdout)
stay available through the typed ``raw`` attribute.

Callers are responsible for the ci-unavailable short-circuit (``svc is None``)
before entering the façade — see ``ci_write_required_result`` in
:mod:`tools.core.ci_runtime`.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Generic, Literal, Sequence, TypeVar

from code_intelligence._async_bridge import use_sandbox_io_loop
from tools.core.ci_attribution import (
    agent_attribution_from_context,
    rebind_ci_service,
    resolved_agent_id,
)

if TYPE_CHECKING:
    from code_intelligence.types import OperationResult
    from tools.core.base import ToolExecutionContext


T = TypeVar("T")

CommitOp = Literal["write", "edit", "delete", "move"]


@dataclass(frozen=True, kw_only=True)
class FileChangeResult(Generic[T]):
    """Commit-audit slice returned by every OCC-gated tool façade.

    ``changed_paths`` is the authoritative set the service actually committed.
    Post-hooks read this key (plus ``ambient_changed_paths`` for cmd) to run
    scope and audit checks uniformly across tools.

    ``raw`` is the service-level return object and carries details that are
    tool-specific (per-spec errors, stdout, abort-kind status). Kept typed so
    callers that need it don't fall back to ``Any``.
    """

    success: bool
    changed_paths: tuple[str, ...]
    raw: T
    ambient_changed_paths: tuple[str, ...] = ()
    conflict_reason: str | None = None


def _dedup_sorted(raw: Any) -> tuple[str, ...]:
    """Normalize a path list from ``svc.cmd``: str, strip empties, sort, dedup."""
    if not isinstance(raw, list):
        return ()
    return tuple(sorted({str(p) for p in raw if str(p or "").strip()}))


def _operation_paths(result: Any, fallback: Sequence[str]) -> tuple[str, ...]:
    files = getattr(result, "files", None)
    if isinstance(files, (list, tuple)):
        paths = tuple(
            str(getattr(item, "file_path", "") or "")
            for item in files
            if str(getattr(item, "file_path", "") or "").strip()
        )
        if paths:
            return paths
    return tuple(fallback)


async def submit_commit(
    context: "ToolExecutionContext",
    *,
    op: CommitOp,
    specs: Sequence[Any],
    fallback_paths: Sequence[str],
    description: str,
) -> FileChangeResult["OperationResult"]:
    """Run an OCC commit through ``svc.{op}_file`` and return the audit slice.

    Caller must ensure ``get_ci_service(context)`` is non-None before calling.
    The façade does the sync-in-thread wrap, sandbox I/O loop binding, and
    ci-service rebind that every commit tool previously duplicated.
    """
    from tools.core.ci_runtime import get_ci_service

    svc = get_ci_service(context)
    if svc is None:
        raise RuntimeError(
            "submit_commit requires an active ci_service; "
            "caller must short-circuit with ci_write_required_result first",
        )

    method = getattr(svc, f"{op}_file")

    rebind_ci_service(context, svc)
    with use_sandbox_io_loop():
        result = await asyncio.to_thread(
            method,
            list(specs),
            agent_id=resolved_agent_id(context),
            description=description,
        )

    paths = _operation_paths(result, fallback_paths)
    conflict = str(getattr(result, "conflict_reason", "") or "")
    return FileChangeResult(
        success=bool(getattr(result, "success", False)),
        changed_paths=paths,
        conflict_reason=conflict or None,
        raw=result,
    )


async def submit_codeact_cmd(
    context: "ToolExecutionContext",
    *,
    command: str,
    description: str,
    timeout: int | None = None,
    sandbox: Any | None = None,
    attribute_changes: bool = True,
) -> FileChangeResult[SimpleNamespace]:
    """Run a CodeAct shell/python command through ``svc.cmd`` and return the audit slice.

    Python code is wrapped into a bash invocation by the caller before it
    reaches here — the façade only sees the final bash ``command`` string.
    When *sandbox* is ``None`` the façade reads it from ``context.metadata``;
    pass an explicit handle when the caller already performed sandbox recovery
    and holds a fresher handle than the context knows about.

    Attribution (agent_id, team_run_id, agent_run_id, task_id) is pulled from
    the context via :func:`agent_attribution_from_context` — callers do not
    build it themselves.

    Success is ``exit_code == 0 AND overlay_commit_status in (None, 'committed')``.
    """
    from tools.core.ci_runtime import get_ci_service

    svc = get_ci_service(context)
    if svc is None:
        raise RuntimeError(
            "submit_codeact_cmd requires an active ci_service; "
            "caller must short-circuit before entering the façade",
        )
    resolved_sandbox = sandbox
    if resolved_sandbox is None:
        resolved_sandbox = context.metadata.get("ci_sandbox") or context.metadata.get(
            "daytona_sandbox",
        )
    if resolved_sandbox is None:
        raise RuntimeError(
            "submit_codeact_cmd requires a sandbox in context.metadata "
            "(ci_sandbox or daytona_sandbox) or as an explicit argument",
        )

    attribution = agent_attribution_from_context(context)
    response = await svc.cmd(
        resolved_sandbox,
        command,
        timeout=timeout,
        description=description,
        agent_id=attribution.agent_id,
        team_run_id=attribution.team_run_id,
        agent_run_id=attribution.agent_run_id,
        task_id=attribution.task_id,
        attribute_changes=attribute_changes,
    )

    changed = _dedup_sorted(getattr(response, "changed_paths", None))
    ambient = _dedup_sorted(getattr(response, "ambient_changed_paths", None))
    exit_code = int(getattr(response, "exit_code", 1) or 0)
    commit_status = getattr(response, "overlay_commit_status", None)
    conflict_reason = getattr(response, "overlay_conflict_reason", None)

    success = exit_code == 0 and (commit_status in (None, "committed"))
    return FileChangeResult(
        success=success,
        changed_paths=changed,
        ambient_changed_paths=ambient,
        conflict_reason=(str(conflict_reason) if conflict_reason else None),
        raw=response,
    )


__all__ = [
    "CommitOp",
    "FileChangeResult",
    "submit_codeact_cmd",
    "submit_commit",
]
