"""Daytona-backed file and folder delete/move tools.

These tools validate requested paths under the repo root and submit the
operation through the code-intelligence OCC commit path. ``is_folder=True`` is
passed as service-level intent; the code-intelligence mutation service expands
folder members and applies the OCC gate to each member file. Overwrite
semantics are enforced by the platform pre-hook, not by this tool.

CodeAct's shell policy blocks ``rm`` / ``mv`` precisely so that deletions
and moves flow through these OCC-gated tools instead of the unaudited
shell path.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from code_intelligence.types import DeleteSpec, MoveSpec
from tools.core.base import ToolExecutionContext, ToolResult
from tools.core.ci_runtime import (
    ci_write_required_result,
    get_ci_service,
)
from tools.core.decorator import tool
from tools.daytona_toolkit._commit import submit_commit
from tools.daytona_toolkit._daytona_utils import _resolve_path


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


def _failure_status(result: Any, *, move: bool) -> tuple[str, str]:
    status = str(getattr(result, "status", "") or "failed")
    conflict_reason = str(getattr(result, "conflict_reason", "") or "")
    if conflict_reason == "not_found":
        return "not_found", "not_found"
    if move and conflict_reason == "dst_exists":
        return "dst_exists", "dst_exists"
    return status, conflict_reason or status


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
    warnings: list[str] = []

    svc = get_ci_service(context)
    if svc is None:
        return ci_write_required_result("daytona_delete_file", resolved)

    specs = [DeleteSpec(path=resolved, is_folder=is_folder)]

    change = await submit_commit(
        context,
        op="delete",
        specs=specs,
        fallback_paths=[resolved],
        description=f"delete {resolved}",
    )
    paths = list(change.changed_paths)
    common_metadata = {
        "changed_paths": paths,
        "ambient_changed_paths": list(change.ambient_changed_paths),
        "conflict_reason": change.conflict_reason,
    }
    if change.success:
        return ToolResult(
            output=_operation_payload(
                status="deleted",
                paths=paths,
                warnings=warnings,
            ),
            metadata={
                "file_count": len(paths),
                "success_count": len(paths),
                **common_metadata,
            },
        )

    payload_status, conflict_reason = _failure_status(change.raw, move=False)
    return ToolResult(
        output=_operation_payload(
            status=payload_status,
            paths=paths,
            warnings=warnings,
            conflict_reason=conflict_reason,
            message=str(change.conflict_reason or conflict_reason),
        ),
        is_error=True,
        metadata={
            "file_count": len(paths),
            "success_count": 0,
            **common_metadata,
        },
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
        "semantics are enforced by the platform pre-hook. Use this "
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
    warnings: list[str] = []

    svc = get_ci_service(context)
    if svc is None:
        return ci_write_required_result("daytona_move_file", src_resolved)

    specs = [
        MoveSpec(
            src_path=src_resolved,
            dst_path=dst_resolved,
            overwrite=False,
            is_folder=is_folder,
        ),
    ]
    fallback_paths = [s.src_path for s in specs] + [s.dst_path for s in specs]

    change = await submit_commit(
        context,
        op="move",
        specs=specs,
        fallback_paths=fallback_paths,
        description=f"move {src_resolved} -> {dst_resolved}",
    )
    paths = list(change.changed_paths)
    common_metadata = {
        "changed_paths": paths,
        "ambient_changed_paths": list(change.ambient_changed_paths),
        "conflict_reason": change.conflict_reason,
    }

    if change.success:
        return ToolResult(
            output=_move_payload(
                status="moved",
                src=src_resolved,
                dst=dst_resolved,
                paths=paths,
                warnings=warnings,
            ),
            metadata={
                "file_count": len(paths),
                "success_count": len(paths),
                **common_metadata,
            },
        )

    payload_status, conflict_reason = _failure_status(change.raw, move=True)
    return ToolResult(
        output=_move_payload(
            status=payload_status,
            src=src_resolved,
            dst=dst_resolved,
            paths=paths,
            warnings=warnings,
            conflict_reason=conflict_reason,
            message=str(change.conflict_reason or conflict_reason),
        ),
        is_error=True,
        metadata={
            "file_count": len(paths),
            "success_count": 0,
            **common_metadata,
        },
    )
