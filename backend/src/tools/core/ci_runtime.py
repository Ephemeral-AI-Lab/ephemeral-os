"""Shared code-intelligence runtime helpers used across toolkits.

Explicit file mutation tools use :func:`commit_ci_operation` to commit one
logical operation against captured file bases. Process-backed tools use
:func:`exec_ci_process_operation` so the CI service can audit the command as
one operation around the underlying ``process.exec`` call.
"""

from __future__ import annotations

import dataclasses
import hashlib
import inspect
import logging
from collections.abc import Sequence
from typing import Any

from code_intelligence.types import OperationResult, OperationChange
from tools.core.base import ToolExecutionContext, ToolResult

logger = logging.getLogger(__name__)


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


@dataclasses.dataclass(frozen=True)
class CiOperationChange:
    """One file slot in a tool-level OCC operation."""

    file_path: str
    base_content: str | None
    final_content: str | None
    base_existed: bool = True


def get_ci_service(context: ToolExecutionContext) -> Any | None:
    """Get the CodeIntelligenceService from context, or None if unavailable."""
    return context.metadata.get("ci_service")


def ci_required_result(tool_name: str, detail: str) -> ToolResult:
    """Build a consistent error for tools that require CI/OCC."""
    suffix = str(detail or "").strip()
    return ToolResult(
        output=(
            f"{tool_name}: Code intelligence/OCC is unavailable."
            f"{' ' + suffix if suffix else ''}"
        ),
        is_error=True,
        metadata={"occ_required": True},
    )


def occ_required_result(
    tool_name: str,
    file_path: str,
    *,
    conflict: bool = False,
) -> ToolResult:
    """Build a consistent OCC-required file-write error."""
    metadata = {"occ_required": True}
    if conflict:
        metadata["conflict"] = True
    operation = "Write" if "write" in tool_name else "Edit"
    return ToolResult(
        output=(
            f"{tool_name}: Code intelligence/OCC is unavailable. "
            f"{operation} of {file_path} is disabled. Direct sandbox write fallback is disabled."
        ),
        is_error=True,
        metadata=metadata,
    )


def _team_edit_ids(context: ToolExecutionContext) -> tuple[str, str, str]:
    return (
        str(context.metadata.get("team_run_id") or ""),
        str(context.metadata.get("agent_run_id") or ""),
        str(context.metadata.get("work_item_id") or ""),
    )


def _coerce_ci_operation_change(change: CiOperationChange | OperationChange) -> OperationChange:
    if isinstance(change, OperationChange):
        return change
    base_content = change.base_content
    base_existed = change.base_existed
    if base_content is None:
        base_content = ""
        if change.final_content is not None:
            base_existed = False
    return OperationChange(
        file_path=change.file_path,
        base_content=base_content,
        base_hash=_content_hash(base_content),
        final_content=change.final_content,
        base_existed=base_existed,
    )


def commit_ci_operation(
    context: ToolExecutionContext,
    changes: Sequence[CiOperationChange | OperationChange],
    *,
    edit_type: str,
    description: str,
    agent_id: str = "",
) -> OperationResult:
    """Unified OCC verification entry point for explicit file operations.

    The caller produces one :class:`CiOperationChange` per file it means to
    mutate (single-file tools pass a one-element list); the set commits
    atomically against the supplied base snapshots via
    :meth:`CodeIntelligenceService.commit_operation_against_base`.
    """
    svc = get_ci_service(context)
    if svc is None:
        raise RuntimeError("Code intelligence/OCC is unavailable")
    if not hasattr(svc, "commit_operation_against_base"):
        raise RuntimeError("CI service does not support OCC operation commits")

    operation_changes = tuple(_coerce_ci_operation_change(change) for change in changes)
    result = svc.commit_operation_against_base(
        operation_changes,
        agent_id=_resolved_agent_id(context, preferred=agent_id),
        edit_type=edit_type,
        description=description,
    )
    finalize_ci_operation_result(
        context,
        result=result,
        changes=operation_changes,
        edit_type=edit_type,
        description=description,
        ci_arbiter=getattr(svc, "arbiter", None),
    )
    return result


async def exec_ci_process_operation(
    context: ToolExecutionContext,
    sandbox: Any,
    command: str,
    *,
    timeout: int | None = None,
    description: str,
    edit_type: str = "process",
) -> Any:
    """Run one process command through the OCC-aware execution entry point.

    CodeAct delegates command execution here; lower layers run the command
    and audit the complete process operation.
    """
    svc = get_ci_service(context)
    if svc is None:
        raise RuntimeError("Code intelligence/OCC is unavailable")

    audited_exec_descriptor = inspect.getattr_static(svc, "exec_process_operation", None)
    audited_exec = (
        getattr(svc, "exec_process_operation", None)
        if audited_exec_descriptor is not None
        else None
    )
    if callable(audited_exec):
        response = audited_exec(
            sandbox,
            command,
            timeout=timeout,
            description=description,
            edit_type=edit_type,
            agent_id=_resolved_agent_id(context),
            team_run_id=str(context.metadata.get("team_run_id") or ""),
            agent_run_id=str(context.metadata.get("agent_run_id") or ""),
            task_id=str(context.metadata.get("work_item_id") or ""),
        )
    else:
        process = getattr(sandbox, "process", None)
        exec_fn = getattr(process, "exec", None) if process is not None else None
        if not callable(exec_fn):
            raise RuntimeError("Sandbox process.exec is unavailable")
        response = exec_fn(command, timeout=timeout) if timeout is not None else exec_fn(command)
    if inspect.isawaitable(response):
        return await response
    return response


def _note_team_memory_conflict(
    context: ToolExecutionContext,
    *,
    file_path: str,
    reason: str,
) -> None:
    """Persist a typed conflict event when a TeamRun is active."""
    team_run_id = context.metadata.get("team_run_id")
    if not team_run_id:
        return
    team_run = _get_team_run(str(team_run_id))
    if team_run is None or not hasattr(team_run, "note_conflict_event"):
        return
    try:
        team_run.note_conflict_event(
            file_path=file_path,
            reason=reason,
            work_item_id=str(context.metadata.get("work_item_id") or ""),
            agent_name=str(context.metadata.get("agent_name") or ""),
        )
    except Exception:
        logger.debug("team memory conflict persistence failed for %s", file_path, exc_info=True)


def _resolved_agent_id(context: ToolExecutionContext, *, preferred: str = "") -> str:
    agent_id = str(preferred or "").strip()
    if agent_id:
        return agent_id
    agent_name = str(context.metadata.get("agent_name") or "").strip()
    if agent_name:
        return agent_name
    return str(context.metadata.get("agent_run_id") or "").strip()


def finalize_ci_operation_result(
    context: ToolExecutionContext,
    *,
    result: Any,
    changes: Sequence[OperationChange],
    edit_type: str,
    description: str,
    ci_arbiter: Any | None,
    hashes_by_path: dict[str, tuple[str, str]] | None = None,
) -> None:
    if bool(getattr(result, "success", False)):
        _, agent_run_id, task_id = _team_edit_ids(context)
        successful_files = {
            str(getattr(file_result, "file_path", "") or "")
            for file_result in getattr(result, "files", ()) or ()
            if bool(getattr(file_result, "success", False))
        }
        if not successful_files and changes:
            successful_files = {change.file_path for change in changes}
        for change in changes:
            if change.file_path not in successful_files:
                continue
            old_hash, new_hash = (
                hashes_by_path.get(change.file_path, ("", ""))
                if hashes_by_path is not None
                else ("", "")
            )
            if not old_hash:
                old_hash = _content_hash(change.base_content) if change.base_existed else ""
            if not new_hash:
                new_hash = (
                    _content_hash(change.final_content)
                    if change.final_content is not None
                    else ""
                )
            _propagate_team_edit(
                context,
                file_path=change.file_path,
                agent_run_id=agent_run_id,
                task_id=task_id,
                edit_type=edit_type,
                old_hash=old_hash,
                new_hash=new_hash,
                description=description,
                ci_arbiter=ci_arbiter,
            )
        return

    conflict_file = str(getattr(result, "conflict_file", "") or "")
    conflict_reason = str(
        getattr(result, "conflict_reason", "")
        or getattr(result, "status", "")
        or "write conflict"
    )
    if conflict_file:
        _note_team_memory_conflict(
            context,
            file_path=conflict_file,
            reason=conflict_reason,
        )


def _propagate_team_edit(
    context: ToolExecutionContext,
    *,
    file_path: str,
    agent_run_id: str,
    task_id: str,
    edit_type: str,
    old_hash: str,
    new_hash: str,
    description: str,
    ci_arbiter: Any | None,
) -> None:
    """Mirror successful edits into the team-run coordination stream."""
    team_run_id = str(context.metadata.get("team_run_id") or "")
    if not team_run_id or not file_path:
        return
    team_run = _get_team_run(team_run_id)
    if team_run is None:
        return

    store = getattr(team_run, "arbiter", None)
    if (
        store is not None
        and getattr(store, "initialized", False)
        and store is not ci_arbiter
    ):
        try:
            store.record_edit(
                file_path=file_path,
                team_run_id=team_run_id,
                agent_run_id=agent_run_id,
                task_id=task_id,
                edit_type=edit_type,
                old_hash=old_hash,
                new_hash=new_hash,
                description=description,
            )
        except Exception:
            logger.debug("team arbiter mirror failed for %s", file_path, exc_info=True)


def _get_team_run(team_run_id: str) -> Any | None:
    try:
        from team.runtime.registry import get as get_team_run
    except Exception:
        return None
    try:
        return get_team_run(team_run_id)
    except Exception:
        return None


__all__ = [
    "CiOperationChange",
    "ci_required_result",
    "commit_ci_operation",
    "exec_ci_process_operation",
    "finalize_ci_operation_result",
    "get_ci_service",
    "occ_required_result",
]
