"""Move file tool."""

from __future__ import annotations

from pydantic import BaseModel, Field

from sandbox.api.models import MoveFileRequest
from tools.core.base import ToolExecutionContextService, ToolResult
from tools.core.decorator import tool
from tools.core.sandbox_session import (
    actor_from_context,
    normalized_path,
    resolve_sandbox_path,
    sandbox_api_or_error,
    sandbox_id_or_error,
)
from tools.sandbox_toolkit._operation_payloads import move_payload


class MoveFileInput(BaseModel):
    src_path: str = Field(
        ...,
        min_length=1,
        description="Repo-relative or sandbox-root source path.",
    )
    target_path: str = Field(
        ...,
        min_length=1,
        description="Repo-relative or sandbox-root destination path.",
    )
    is_folder: bool = Field(
        default=False,
        description="False moves one file. True moves a folder tree.",
    )


class MoveFileOutput(BaseModel):
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
        description="Paths changed by the move.",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Advisory warnings emitted by the operation.",
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
    name="move_file",
    description=(
        "Move or rename a file, or a folder tree with `is_folder=True`. Atomic via the commit "
        "pipeline. Prefer over `shell mv`. Refuses to overwrite an existing destination "
        "(`status: dst_exists`) — delete it first if intended. There is no copy tool; for a "
        "copy, `read_file` then `write_file`. Parent of target must exist."
    ),
    short_description="Move a file or folder.",
    input_model=MoveFileInput,
    output_model=MoveFileOutput,
)
async def move_file(
    src_path: str,
    target_path: str,
    is_folder: bool = False,
    *,
    context: ToolExecutionContextService,
) -> ToolResult:
    """Move a file or folder."""
    src_resolved = normalized_path(resolve_sandbox_path(src_path, context))
    dst_resolved = normalized_path(resolve_sandbox_path(target_path, context))
    warnings: list[str] = []

    sandbox_id, sandbox_id_error = sandbox_id_or_error(context)
    if sandbox_id_error is not None:
        return sandbox_id_error
    api, api_error = sandbox_api_or_error(context, tool_name="move_file")
    if api_error is not None:
        return api_error

    result = await api.move_file(
        sandbox_id,
        MoveFileRequest(
            src_path=src_resolved,
            dst_path=dst_resolved,
            overwrite=False,
            is_folder=is_folder,
            actor=actor_from_context(context),
            description=f"move {src_resolved} -> {dst_resolved}",
        ),
    )
    paths = list(result.changed_paths or (src_resolved, dst_resolved))

    if result.success:
        return ToolResult(
            output=move_payload(
                status="moved",
                src=src_resolved,
                dst=dst_resolved,
                paths=paths,
                warnings=warnings,
            ),
            metadata={
                "file_count": len(paths),
                "success_count": len(paths),
                "changed_paths": paths,
                "conflict_reason": None,
            },
        )

    payload_status = _move_failure_status(result.conflict_reason)
    return ToolResult(
        output=move_payload(
            status=payload_status,
            src=src_resolved,
            dst=dst_resolved,
            paths=paths,
            warnings=warnings,
            conflict_reason=result.conflict_reason,
            message=str(result.conflict_reason or payload_status),
        ),
        is_error=True,
        metadata={
            "file_count": len(paths),
            "success_count": 0,
            "changed_paths": paths,
            "conflict_reason": result.conflict_reason,
        },
    )


def _move_failure_status(conflict_reason: str | None) -> str:
    if conflict_reason in {"dst_exists", "destination_exists", "exists"}:
        return "dst_exists"
    if conflict_reason in {"not_found", "missing"}:
        return "not_found"
    if conflict_reason in {"base_mismatch", "version_conflict", "drift"}:
        return "aborted_version"
    if conflict_reason in {"overlap"}:
        return "aborted_overlap"
    if conflict_reason in {"lock_conflict", "locked"}:
        return "aborted_lock"
    return "failed"


__all__ = ["move_file"]
