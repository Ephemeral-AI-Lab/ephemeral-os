"""Daytona-backed file and folder delete/move tools.

These tools validate requested paths under the repo root and submit the
operation through the code-intelligence OCC commit path. ``is_folder=True``
enumerates every descendant file client-side and submits the whole set as a
single OCC batch (delete) or a remapped :class:`MoveSpec` batch (move); the
service-level OCC gate applies to each member file. Overwrite semantics are
enforced by the tool-guard prehook, not by this tool.

CodeAct's shell policy blocks ``rm`` / ``mv`` precisely so that deletions
and moves flow through these OCC-gated tools instead of the unaudited
shell path.
"""

from __future__ import annotations

import asyncio
import json
import shlex
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from code_intelligence._async_bridge import use_sandbox_io_loop
from code_intelligence.types import MoveSpec
from tools.core.base import ToolExecutionContext, ToolResult
from tools.core.ci_attribution import rebind_ci_service, resolved_agent_id
from tools.core.ci_runtime import (
    ci_write_required_result,
    get_ci_service,
)
from tools.core.decorator import tool
from tools.daytona_toolkit._daytona_utils import (
    _exec_command,
    _extend_write_scope,
    _extract_exit_code,
    _get_repo_root,
    _resolve_path,
    _scope_deny_message,
    _supports_exec_transport,
    _team_repo_scope_deny_errors,
    _wrap_bash_command,
    _write_scope_covers,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _operation_payload(
    *,
    status: str,
    paths: list[str],
    warnings: list[str],
    conflict_reason: str | None = None,
    message: str | None = None,
) -> str:
    payload: dict[str, Any] = {
        "status": status,
        "paths": paths,
        "warnings": warnings,
    }
    if conflict_reason:
        payload["conflict_reason"] = conflict_reason
    if message:
        payload["message"] = message
    return json.dumps(payload)


def _move_payload(
    *,
    status: str,
    src: str,
    dst: str,
    warnings: list[str],
    paths: list[str] | None = None,
    conflict_reason: str | None = None,
    message: str | None = None,
) -> str:
    payload: dict[str, Any] = {
        "status": status,
        "src_path": src,
        "target_path": dst,
        "paths": (
            paths
            if paths is not None
            else ([src, dst] if status == "moved" else [])
        ),
        "warnings": warnings,
    }
    if conflict_reason:
        payload["conflict_reason"] = conflict_reason
    if message:
        payload["message"] = message
    return json.dumps(payload)


def _normalized_path(path: str) -> str:
    if path == "/":
        return path
    return path.rstrip("/") or path


def _repo_guard_error(
    context: ToolExecutionContext,
    file_path: str,
    *,
    tool_name: str,
) -> str | None:
    """Reject mutations outside a concrete repo root."""
    repo_root = _normalized_path(str(_get_repo_root(context) or ""))
    if not repo_root or repo_root == "/":
        return (
            f"{tool_name}: operation requires a non-root "
            "repo_root/daytona_cwd in context."
        )

    path = _normalized_path(file_path)
    if path == repo_root:
        return f"{tool_name}: refusing to operate on repo root: {repo_root}"
    if not path.startswith(repo_root + "/"):
        return (
            f"{tool_name}: refusing operation outside repo root "
            f"{repo_root}: {file_path}"
        )
    return None


def _operation_paths(result: Any, fallback: list[str]) -> list[str]:
    files = getattr(result, "files", None)
    if isinstance(files, (list, tuple)):
        paths = [
            str(getattr(item, "file_path", "") or "")
            for item in files
            if str(getattr(item, "file_path", "") or "").strip()
        ]
        if paths:
            return paths
    return fallback


def _failure_status(result: Any, *, move: bool) -> tuple[str, str]:
    status = str(getattr(result, "status", "") or "failed")
    conflict_reason = str(getattr(result, "conflict_reason", "") or "")
    if conflict_reason == "not_found":
        return "not_found", "not_found"
    if move and conflict_reason == "dst_exists":
        return "dst_exists", "dst_exists"
    return status, conflict_reason or status


async def _list_folder_files(
    context: ToolExecutionContext, folder: str,
) -> list[str]:
    """Enumerate every regular file under *folder* as absolute paths.

    Mirrors ``ContentManager``'s sandbox/local split: if a Daytona sandbox
    is bound on the context, enumerate via ``find -type f`` inside the
    sandbox so paths align with ``ContentManager.read`` routing. Otherwise
    fall back to local ``Path.rglob``.

    Raises ``FileNotFoundError`` if the folder does not exist and
    ``NotADirectoryError`` if the path is not a directory.
    """
    sandbox = context.metadata.get("daytona_sandbox")
    if sandbox is not None and _supports_exec_transport(sandbox):
        probe_cmd = (
            f"if [ ! -e {shlex.quote(folder)} ]; then echo __MISSING__; "
            f"elif [ ! -d {shlex.quote(folder)} ]; then echo __NOTDIR__; "
            f"else find {shlex.quote(folder)} -type f -print; fi"
        )
        response = await _exec_command(sandbox, _wrap_bash_command(probe_cmd))
        stdout = getattr(response, "result", "") or ""
        cleaned, exit_code = _extract_exit_code(
            stdout, fallback_exit_code=getattr(response, "exit_code", None),
        )
        if exit_code not in (0, None):
            raise RuntimeError(cleaned or f"enumerate failed for {folder}")
        lines = [line for line in cleaned.splitlines() if line.strip()]
        if lines and lines[0].strip() == "__MISSING__":
            raise FileNotFoundError(folder)
        if lines and lines[0].strip() == "__NOTDIR__":
            raise NotADirectoryError(folder)
        return lines
    root = Path(folder)
    if not root.exists():
        raise FileNotFoundError(folder)
    if not root.is_dir():
        raise NotADirectoryError(folder)
    return sorted(str(p) for p in root.rglob("*") if p.is_file())


# ---------------------------------------------------------------------------
# daytona_delete_file
# ---------------------------------------------------------------------------


class DaytonaDeleteFileInput(BaseModel):
    path: str = Field(
        ...,
        min_length=1,
        description="Path to delete. Must exist at call time.",
    )
    is_folder: bool = Field(
        default=False,
        description=(
            "Set True to delete a directory tree. The tool enumerates every "
            "descendant regular file under the folder and submits them as a "
            "single OCC batch; base-hash drift on any member aborts the "
            "whole batch. False (default) deletes a single file."
        ),
    )


class DaytonaDeleteFileOutput(BaseModel):
    status: str = Field(
        ...,
        description=(
            "`deleted`, `not_found`, `aborted_version`, `aborted_lock`, or `failed`."
        ),
    )
    paths: list[str] = Field(
        default_factory=list,
        description="Paths affected by the OCC commit.",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Advisory warnings (e.g., files outside write_scope).",
    )
    conflict_reason: str | None = Field(
        default=None,
        description="Short reason when status is an abort class.",
    )
    message: str | None = Field(
        default=None,
        description="Human-readable detail.",
    )


@tool(
    name="daytona_delete_file",
    description=(
        "Delete a file (default) or a folder tree (`is_folder=True`) through "
        "the OCC-gated code-intelligence commit path. Folder deletes "
        "enumerate every descendant file and submit them as one OCC batch; "
        "base-hash drift on any member aborts the whole batch with "
        "`aborted_version`. Use this instead of `rm` in CodeAct; CodeAct "
        "`rm` is blocked so deletes stay coordinated."
    ),
    short_description="Delete a file or folder through the OCC commit path.",
    input_model=DaytonaDeleteFileInput,
    output_model=DaytonaDeleteFileOutput,
)
async def daytona_delete_file(
    path: str,
    is_folder: bool = False,
    *,
    context: ToolExecutionContext,
) -> ToolResult:
    """Delete a file or folder through the code-intelligence OCC commit path."""
    resolved = _normalized_path(_resolve_path(path, context))
    warnings: list[str] = list(context.metadata.get("guard_pre_warnings") or [])

    svc = get_ci_service(context)
    if svc is None:
        return ci_write_required_result("daytona_delete_file", resolved)

    guard_error = _repo_guard_error(
        context, resolved, tool_name="daytona_delete_file",
    )
    if guard_error is not None:
        return ToolResult(
            output=_operation_payload(
                status="failed",
                paths=[resolved],
                warnings=warnings,
                message=guard_error,
            ),
            is_error=True,
        )

    if is_folder:
        try:
            paths_to_delete = await _list_folder_files(context, resolved)
        except FileNotFoundError:
            return ToolResult(
                output=_operation_payload(
                    status="not_found",
                    paths=[resolved],
                    warnings=warnings,
                    conflict_reason="not_found",
                ),
                is_error=True,
                metadata={"file_count": 0, "success_count": 0},
            )
        except NotADirectoryError:
            return ToolResult(
                output=_operation_payload(
                    status="failed",
                    paths=[resolved],
                    warnings=warnings,
                    message=(
                        "daytona_delete_file: is_folder=True but path is a "
                        f"file: {resolved}"
                    ),
                ),
                is_error=True,
            )
        if not paths_to_delete:
            return ToolResult(
                output=_operation_payload(
                    status="deleted",
                    paths=[],
                    warnings=warnings,
                ),
                metadata={"file_count": 0, "success_count": 0},
            )
        member_offenders = _team_repo_scope_deny_errors(
            context, paths_to_delete, tool_name="daytona_delete_file",
        )
        if member_offenders:
            return ToolResult(
                output=_operation_payload(
                    status="failed",
                    paths=[path for path, _ in member_offenders],
                    warnings=warnings,
                    message=_scope_deny_message(
                        member_offenders,
                        tool_name="daytona_delete_file",
                        role="folder members",
                    ),
                ),
                is_error=True,
            )
        paths_to_commit = paths_to_delete
    else:
        paths_to_commit = [resolved]

    rebind_ci_service(context, svc)
    with use_sandbox_io_loop():
        result = await asyncio.to_thread(
            svc.delete_file,
            paths_to_commit,
            agent_id=resolved_agent_id(context),
            description=f"delete {resolved}",
        )
    if getattr(result, "success", False):
        paths = _operation_paths(result, paths_to_commit)
        return ToolResult(
            output=_operation_payload(
                status="deleted",
                paths=paths,
                warnings=warnings,
            ),
            metadata={"file_count": len(paths), "success_count": len(paths)},
        )

    payload_status, conflict_reason = _failure_status(result, move=False)
    paths = _operation_paths(result, paths_to_commit)
    return ToolResult(
        output=_operation_payload(
            status=payload_status,
            paths=paths,
            warnings=warnings,
            conflict_reason=conflict_reason,
            message=str(getattr(result, "conflict_reason", "") or conflict_reason),
        ),
        is_error=True,
        metadata={"file_count": len(paths), "success_count": 0},
    )


# ---------------------------------------------------------------------------
# daytona_move_file
# ---------------------------------------------------------------------------


class DaytonaMoveFileInput(BaseModel):
    src_path: str = Field(
        ...,
        min_length=1,
        description="Source path. Must exist at call time.",
    )
    target_path: str = Field(
        ...,
        min_length=1,
        description="Destination path.",
    )
    is_folder: bool = Field(
        default=False,
        description=(
            "Set True to move a directory tree. The tool enumerates every "
            "descendant regular file under src, remaps src-prefix to "
            "target-prefix, and submits the whole remapping as one OCC "
            "batch. False (default) moves a single file."
        ),
    )


class DaytonaMoveFileOutput(BaseModel):
    status: str = Field(
        ...,
        description=(
            "`moved`, `dst_exists`, `not_found`, `aborted_version`, "
            "`aborted_overlap`, `aborted_lock`, or `failed`."
        ),
    )
    src_path: str = Field(..., description="Resolved source path.")
    target_path: str = Field(..., description="Resolved destination path.")
    paths: list[str] = Field(
        default_factory=list,
        description="Paths affected by the OCC commit.",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Advisory warnings (e.g., files outside write_scope).",
    )
    conflict_reason: str | None = Field(
        default=None,
        description="Short reason when status is an abort class.",
    )
    message: str | None = Field(
        default=None,
        description="Human-readable detail.",
    )


@tool(
    name="daytona_move_file",
    description=(
        "Move a file (default) or a folder tree (`is_folder=True`) through "
        "the OCC-gated code-intelligence commit path. Folder moves "
        "enumerate every descendant file, remap the src prefix to the "
        "target prefix, and submit the whole batch atomically. Base-hash "
        "drift on any member aborts with `aborted_version`. Overwrite "
        "semantics are enforced by the tool-guard prehook. Use this "
        "instead of `mv` in CodeAct; CodeAct `mv` is blocked."
    ),
    short_description="Move a file or folder through the OCC commit path.",
    input_model=DaytonaMoveFileInput,
    output_model=DaytonaMoveFileOutput,
)
async def daytona_move_file(
    src_path: str,
    target_path: str,
    is_folder: bool = False,
    *,
    context: ToolExecutionContext,
) -> ToolResult:
    """Move a file or folder through the code-intelligence OCC commit path."""
    src_resolved = _normalized_path(_resolve_path(src_path, context))
    dst_resolved = _normalized_path(_resolve_path(target_path, context))

    # src_in_scope controls post-success write_scope widening: a move
    # whose src was owned stays owned at dst (see _extend_write_scope below).
    src_in_scope = _write_scope_covers(context, src_resolved)
    warnings: list[str] = list(context.metadata.get("guard_pre_warnings") or [])

    svc = get_ci_service(context)
    if svc is None:
        return ci_write_required_result("daytona_move_file", src_resolved)

    guard_errors = [
        _repo_guard_error(context, src_resolved, tool_name="daytona_move_file"),
        _repo_guard_error(context, dst_resolved, tool_name="daytona_move_file"),
    ]
    guard_error = next((err for err in guard_errors if err is not None), None)
    if guard_error is None and dst_resolved == src_resolved:
        guard_error = "daytona_move_file: src_path and target_path are identical"
    if guard_error is None and dst_resolved.startswith(src_resolved + "/"):
        guard_error = (
            "daytona_move_file: refusing to move a path to a destination "
            f"inside source: {dst_resolved}"
        )
    if guard_error is None and src_resolved.startswith(dst_resolved + "/"):
        guard_error = (
            "daytona_move_file: refusing to replace a destination that "
            f"contains source: {dst_resolved}"
        )
    if guard_error is not None:
        return ToolResult(
            output=_move_payload(
                status="failed",
                src=src_resolved,
                dst=dst_resolved,
                paths=[],
                warnings=warnings,
                message=guard_error,
            ),
            is_error=True,
        )

    if is_folder:
        try:
            members = await _list_folder_files(context, src_resolved)
        except FileNotFoundError:
            return ToolResult(
                output=_move_payload(
                    status="not_found",
                    src=src_resolved,
                    dst=dst_resolved,
                    paths=[],
                    warnings=warnings,
                    conflict_reason="not_found",
                ),
                is_error=True,
                metadata={"file_count": 0, "success_count": 0},
            )
        except NotADirectoryError:
            return ToolResult(
                output=_move_payload(
                    status="failed",
                    src=src_resolved,
                    dst=dst_resolved,
                    paths=[],
                    warnings=warnings,
                    message=(
                        "daytona_move_file: is_folder=True but src_path is a "
                        f"file: {src_resolved}"
                    ),
                ),
                is_error=True,
            )
        if not members:
            return ToolResult(
                output=_move_payload(
                    status="moved",
                    src=src_resolved,
                    dst=dst_resolved,
                    paths=[],
                    warnings=warnings,
                ),
                metadata={"file_count": 0, "success_count": 0},
            )
        member_offenders = _team_repo_scope_deny_errors(
            context, members, tool_name="daytona_move_file",
        )
        if member_offenders:
            return ToolResult(
                output=_move_payload(
                    status="failed",
                    src=src_resolved,
                    dst=dst_resolved,
                    paths=[path for path, _ in member_offenders],
                    warnings=warnings,
                    message=_scope_deny_message(
                        member_offenders,
                        tool_name="daytona_move_file",
                        role="folder members",
                    ),
                ),
                is_error=True,
            )
        src_prefix_len = len(src_resolved)
        specs = [
            MoveSpec(
                src_path=member,
                dst_path=dst_resolved + member[src_prefix_len:],
            )
            for member in members
        ]
    else:
        specs = [
            MoveSpec(src_path=src_resolved, dst_path=dst_resolved, overwrite=False),
        ]
    fallback_paths = [s.src_path for s in specs] + [s.dst_path for s in specs]

    rebind_ci_service(context, svc)
    with use_sandbox_io_loop():
        result = await asyncio.to_thread(
            svc.move_file,
            specs,
            agent_id=resolved_agent_id(context),
            description=f"move {src_resolved} -> {dst_resolved}",
        )

    if getattr(result, "success", False):
        paths = _operation_paths(result, fallback_paths)
        if src_in_scope:
            _extend_write_scope(context, dst_resolved)
        return ToolResult(
            output=_move_payload(
                status="moved",
                src=src_resolved,
                dst=dst_resolved,
                paths=paths,
                warnings=warnings,
            ),
            metadata={"file_count": len(paths), "success_count": len(paths)},
        )

    payload_status, conflict_reason = _failure_status(result, move=True)
    paths = _operation_paths(result, fallback_paths)
    return ToolResult(
        output=_move_payload(
            status=payload_status,
            src=src_resolved,
            dst=dst_resolved,
            paths=paths,
            warnings=warnings,
            conflict_reason=conflict_reason,
            message=str(getattr(result, "conflict_reason", "") or conflict_reason),
        ),
        is_error=True,
        metadata={"file_count": len(paths), "success_count": 0},
    )
