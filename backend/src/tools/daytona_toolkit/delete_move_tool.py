"""Daytona-backed file delete and move tools.

These tools validate requested paths under the repo root, generate one shell
command for the requested file/folder operation, and submit it through
``exec_ci_process_operation`` so the process auditor records changed files.

CodeAct's shell policy blocks ``rm`` / ``mv`` precisely so that deletions and
moves flow through these audited tools instead of the unaudited shell path.
"""

from __future__ import annotations

import json
import logging
import posixpath
import shlex
from typing import Any

from pydantic import BaseModel, Field

from tools.core.base import ToolExecutionContext, ToolResult
from tools.core.ci_runtime import (
    ci_write_required_result,
    exec_ci_process_operation,
    get_ci_service,
)
from tools.core.decorator import tool
from tools.daytona_toolkit._daytona_utils import (
    _extract_exit_code,
    _get_repo_root,
    _require_sandbox,
    _resolve_path,
    _team_repo_write_error,
    _team_repo_write_warning,
    _wrap_bash_command,
    record_coordination_warning,
)

logger = logging.getLogger(__name__)

_SHELL_TIMEOUT_SECONDS = 120
_EXIT_NOT_FOUND = 66
_EXIT_RECURSIVE_REQUIRED = 73
_EXIT_DST_EXISTS = 74


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _scope_checks(
    context: ToolExecutionContext,
    file_path: str,
    *,
    tool_name: str,
) -> tuple[str | None, str | None]:
    """Apply write-scope policy; return ``(hard_error, soft_warning)``."""
    err = _team_repo_write_error(context, file_path, tool_name=tool_name)
    if err is not None:
        return err, None
    warn = _team_repo_write_warning(context, file_path, tool_name=tool_name)
    return None, warn


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


def _normalized_path(path: str) -> str:
    if path == "/":
        return path
    return path.rstrip("/") or path


def _shell_guard_error(
    context: ToolExecutionContext,
    file_path: str,
    *,
    tool_name: str,
) -> str | None:
    """Reject shell mutations outside a concrete repo root."""
    repo_root = _normalized_path(str(_get_repo_root(context) or ""))
    if not repo_root or repo_root == "/":
        return (
            f"{tool_name}: shell operation requires a non-root "
            "repo_root/daytona_cwd in context."
        )

    path = _normalized_path(file_path)
    if path == repo_root:
        return f"{tool_name}: refusing to operate on repo root: {repo_root}"
    if not path.startswith(repo_root + "/"):
        return (
            f"{tool_name}: refusing shell operation outside repo root "
            f"{repo_root}: {file_path}"
        )
    return None


def _changed_paths_from_response(response: Any, fallback: list[str]) -> list[str]:
    raw = getattr(response, "changed_paths", None)
    if isinstance(raw, list):
        changed = [str(item) for item in raw if str(item or "").strip()]
        if changed:
            return changed
    return fallback


async def _run_audited_shell_command(
    context: ToolExecutionContext,
    sandbox: Any,
    command: str,
    *,
    description: str,
) -> tuple[str, int, Any]:
    """Submit one generated bash command through the CI process auditor."""
    response = await exec_ci_process_operation(
        context,
        sandbox,
        _wrap_bash_command(command),
        timeout=_SHELL_TIMEOUT_SECONDS,
        description=description,
    )
    stdout = getattr(response, "result", "") or ""
    cleaned, exit_code = _extract_exit_code(
        stdout,
        fallback_exit_code=getattr(response, "exit_code", None),
    )
    return cleaned, exit_code, response


def _shell_delete_command(file_path: str, *, recursive: bool) -> str:
    path = shlex.quote(file_path)
    missing = shlex.quote(f"Path does not exist: {file_path}")
    if recursive:
        return (
            f"if [ ! -e {path} ]; then printf '%s\\n' {missing}; "
            f"exit {_EXIT_NOT_FOUND}; fi; rm -rf -- {path}"
        )
    recursive_required = shlex.quote(
        f"Path is a directory; pass recursive=true to delete recursively: {file_path}"
    )
    return (
        f"if [ ! -e {path} ]; then printf '%s\\n' {missing}; "
        f"exit {_EXIT_NOT_FOUND}; fi; "
        f"if [ -d {path} ]; then printf '%s\\n' {recursive_required}; "
        f"exit {_EXIT_RECURSIVE_REQUIRED}; fi; rm -f -- {path}"
    )


def _shell_move_command(
    src_path: str,
    dst_path: str,
    *,
    recursive: bool,
    overwrite: bool,
) -> str:
    src = shlex.quote(src_path)
    dst = shlex.quote(dst_path)
    dst_parent = shlex.quote(posixpath.dirname(dst_path) or ".")
    missing = shlex.quote(f"Path does not exist: {src_path}")
    recursive_required = shlex.quote(
        f"Path is a directory; pass recursive=true to move recursively: {src_path}"
    )
    dst_exists = shlex.quote(
        f"Destination exists: {dst_path} (pass overwrite=True to replace)"
    )

    parts = [
        (
            f"if [ ! -e {src} ]; then printf '%s\\n' {missing}; "
            f"exit {_EXIT_NOT_FOUND}; fi"
        ),
    ]
    if not recursive:
        parts.append(
            f"if [ -d {src} ]; then printf '%s\\n' {recursive_required}; "
            f"exit {_EXIT_RECURSIVE_REQUIRED}; fi"
        )
    if overwrite:
        remove = "rm -rf" if recursive else "rm -f"
        parts.append(f"if [ -e {dst} ]; then {remove} -- {dst} || exit $?; fi")
    else:
        parts.append(
            f"if [ -e {dst} ]; then printf '%s\\n' {dst_exists}; "
            f"exit {_EXIT_DST_EXISTS}; fi"
        )
    parts.append(f"mkdir -p -- {dst_parent} && mv -T -- {src} {dst}")
    return "; ".join(parts)


def _shell_failure_status(
    exit_code: int,
    *,
    move: bool,
) -> tuple[str, str]:
    if exit_code == _EXIT_NOT_FOUND:
        return "not_found", "not_found"
    if move and exit_code == _EXIT_DST_EXISTS:
        return "dst_exists", "dst_exists"
    if exit_code == _EXIT_RECURSIVE_REQUIRED:
        return "failed", "recursive_required"
    return "failed", f"exit_code_{exit_code}"


# ---------------------------------------------------------------------------
# daytona_delete_file
# ---------------------------------------------------------------------------


class DaytonaDeleteFileInput(BaseModel):
    file_path: str = Field(
        ...,
        min_length=1,
        description="Path to the file or folder to delete. Must exist at call time.",
    )
    recursive: bool = Field(
        default=False,
        description=(
            "Set True only when deleting a folder tree. Recursive deletes are "
            "submitted as one audited bash command after repo-root safety checks."
        ),
    )
    description: str = Field(
        default="",
        description="Optional human-readable description of the delete.",
    )


class DaytonaDeleteFileOutput(BaseModel):
    status: str = Field(
        ...,
        description=(
            "`deleted`, `not_found`, or `failed`."
        ),
    )
    paths: list[str] = Field(
        default_factory=list,
        description="Paths affected, as reported by the process auditor.",
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
        "Delete a file or folder by validating the target under the repo root, "
        "generating one bash command, and submitting it through the audited "
        "CI process operation boundary. Pass recursive=True only for folder "
        "trees; without it, directories are rejected. Use this instead of "
        "attempting `rm` in CodeAct; the shell policy blocks `rm` for that reason."
    ),
    short_description="Delete a file, or a folder tree with recursive=True.",
    input_model=DaytonaDeleteFileInput,
    output_model=DaytonaDeleteFileOutput,
)
async def daytona_delete_file(
    file_path: str,
    recursive: bool = False,
    description: str = "",
    *,
    context: ToolExecutionContext,
) -> ToolResult:
    """Delete a file or folder in the Daytona sandbox through audited exec."""
    file_path = _normalized_path(_resolve_path(file_path, context))
    hard_error, soft_warning = _scope_checks(
        context, file_path, tool_name="daytona_delete_file",
    )
    if hard_error is not None:
        return ToolResult(output=hard_error, is_error=True)

    warnings: list[str] = []
    if soft_warning is not None:
        warnings.append(soft_warning)
        record_coordination_warning(
            context, category="write_scope", message=soft_warning,
        )

    svc = get_ci_service(context)
    if svc is None:
        return ci_write_required_result("daytona_delete_file", file_path)

    try:
        sandbox = await _require_sandbox(context)
    except Exception as exc:
        return ToolResult(output=str(exc), is_error=True)
    svc.rebind_sandbox(sandbox)

    guard_error = _shell_guard_error(
        context, file_path, tool_name="daytona_delete_file",
    )
    if guard_error is not None:
        return ToolResult(
            output=_operation_payload(
                status="failed",
                paths=[file_path],
                warnings=warnings,
                message=guard_error,
            ),
            is_error=True,
        )

    command = _shell_delete_command(file_path, recursive=recursive)
    try:
        stdout, exit_code, response = await _run_audited_shell_command(
            context,
            sandbox,
            command,
            description=description
            or ("delete recursively " if recursive else "delete ")
            + file_path,
        )
    except Exception as exc:
        logger.debug("delete_file raised for %s", file_path, exc_info=True)
        return ToolResult(
            output=_operation_payload(
                status="failed",
                paths=[file_path],
                warnings=warnings,
                message=f"Delete failed: {exc}",
            ),
            is_error=True,
        )

    if exit_code == 0:
        paths = _changed_paths_from_response(response, [file_path])
        return ToolResult(
            output=_operation_payload(
                status="deleted",
                paths=paths,
                warnings=warnings,
            ),
            metadata={"file_count": len(paths), "success_count": len(paths)},
        )

    payload_status, conflict_reason = _shell_failure_status(
        exit_code,
        move=False,
    )
    return ToolResult(
        output=_operation_payload(
            status=payload_status,
            paths=[file_path],
            warnings=warnings,
            conflict_reason=conflict_reason,
            message=stdout or conflict_reason,
        ),
        is_error=True,
        metadata={"file_count": 1, "success_count": 0},
    )


# ---------------------------------------------------------------------------
# daytona_move_file
# ---------------------------------------------------------------------------


class DaytonaMoveFileInput(BaseModel):
    src_path: str = Field(
        ...,
        min_length=1,
        description="Source file or folder path. Must exist at call time.",
    )
    dst_path: str = Field(
        ...,
        min_length=1,
        description="Destination file path. Must not exist unless overwrite=True.",
    )
    overwrite: bool = Field(
        default=False,
        description=(
            "When True, replace an existing destination before moving. For "
            "recursive moves this can remove an existing destination tree."
        ),
    )
    recursive: bool = Field(
        default=False,
        description=(
            "Set True when moving a folder tree. Recursive moves are submitted "
            "as one audited bash command after repo-root safety checks."
        ),
    )
    description: str = Field(
        default="",
        description="Optional human-readable description of the move.",
    )


class DaytonaMoveFileOutput(BaseModel):
    status: str = Field(
        ...,
        description=(
            "`moved`, `dst_exists`, `not_found`, or `failed`."
        ),
    )
    src_path: str = Field(..., description="Resolved source path.")
    dst_path: str = Field(..., description="Resolved destination path.")
    paths: list[str] = Field(
        default_factory=list,
        description="Paths affected, as reported by the process auditor.",
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
        "Move a file or folder by validating source/destination under the "
        "repo root, generating one bash command, and submitting it through "
        "the audited CI process operation boundary. Pass recursive=True only "
        "for folder trees; without it, source directories are rejected. By "
        "default the destination must not exist; pass overwrite=True only "
        "when replacing an existing destination is intended. Use this instead "
        "of attempting `mv` in CodeAct; the shell policy blocks `mv` for that reason."
    ),
    short_description="Move a file, or a folder tree with recursive=True.",
    input_model=DaytonaMoveFileInput,
    output_model=DaytonaMoveFileOutput,
)
async def daytona_move_file(
    src_path: str,
    dst_path: str,
    overwrite: bool = False,
    recursive: bool = False,
    description: str = "",
    *,
    context: ToolExecutionContext,
) -> ToolResult:
    """Move a file or folder in the Daytona sandbox through audited exec."""
    src_resolved = _normalized_path(_resolve_path(src_path, context))
    dst_resolved = _normalized_path(_resolve_path(dst_path, context))

    warnings: list[str] = []
    for path in (src_resolved, dst_resolved):
        hard_error, soft_warning = _scope_checks(
            context, path, tool_name="daytona_move_file",
        )
        if hard_error is not None:
            return ToolResult(output=hard_error, is_error=True)
        if soft_warning is not None:
            warnings.append(soft_warning)
            record_coordination_warning(
                context, category="write_scope", message=soft_warning,
            )

    svc = get_ci_service(context)
    if svc is None:
        return ci_write_required_result("daytona_move_file", src_resolved)

    try:
        sandbox = await _require_sandbox(context)
    except Exception as exc:
        return ToolResult(output=str(exc), is_error=True)
    svc.rebind_sandbox(sandbox)

    guard_errors = [
        _shell_guard_error(context, src_resolved, tool_name="daytona_move_file"),
        _shell_guard_error(context, dst_resolved, tool_name="daytona_move_file"),
    ]
    guard_error = next((err for err in guard_errors if err is not None), None)
    if guard_error is None and dst_resolved == src_resolved:
        guard_error = "daytona_move_file: src_path and dst_path are identical"
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

    command = _shell_move_command(
        src_resolved,
        dst_resolved,
        recursive=recursive,
        overwrite=overwrite,
    )
    try:
        stdout, exit_code, response = await _run_audited_shell_command(
            context,
            sandbox,
            command,
            description=description
            or ("move recursively " if recursive else "move ")
            + f"{src_resolved} -> {dst_resolved}",
        )
    except Exception as exc:
        logger.debug(
            "move_file raised for %s -> %s", src_resolved, dst_resolved, exc_info=True,
        )
        return ToolResult(
            output=_move_payload(
                status="failed",
                src=src_resolved,
                dst=dst_resolved,
                warnings=warnings,
                message=f"Move failed: {exc}",
            ),
            is_error=True,
        )

    if exit_code == 0:
        paths = _changed_paths_from_response(response, [src_resolved, dst_resolved])
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

    payload_status, conflict_reason = _shell_failure_status(
        exit_code,
        move=True,
    )
    return ToolResult(
        output=_move_payload(
            status=payload_status,
            src=src_resolved,
            dst=dst_resolved,
            paths=[],
            warnings=warnings,
            conflict_reason=conflict_reason,
            message=stdout or conflict_reason,
        ),
        is_error=True,
        metadata={"file_count": 2, "success_count": 0},
    )

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
        "dst_path": dst,
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
