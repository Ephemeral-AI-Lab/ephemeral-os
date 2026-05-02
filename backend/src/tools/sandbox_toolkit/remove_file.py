"""Remove file tool."""

from __future__ import annotations

from pydantic import BaseModel, Field

from sandbox.api.models import RemoveFileRequest
from tools.core.base import ToolExecutionContextService, ToolResult
from tools.core.decorator import tool
from tools.core.sandbox_session import (
    actor_from_context,
    normalized_path,
    resolve_sandbox_path,
    sandbox_api_or_error,
    sandbox_id_or_error,
)
from tools.sandbox_toolkit._delete_move_helpers import operation_payload


class RemoveFileInput(BaseModel):
    path: str = Field(
        ...,
        min_length=1,
        description="Repo-relative or sandbox-root file or folder path.",
    )
    is_folder: bool = Field(
        default=False,
        description="False deletes one file. True deletes a folder tree.",
    )


class RemoveFileOutput(BaseModel):
    status: str = Field(
        ...,
        description="`deleted`, `not_found`, `aborted_version`, `aborted_lock`, or `failed`.",
    )
    paths: list[str] = Field(
        default_factory=list,
        description="Paths changed by the delete.",
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
    name="remove_file",
    description=(
        "Remove a file, or a folder tree with `is_folder=True`. Atomic and audited via the "
        "commit pipeline. Prefer over `shell rm` for structured errors and traceability. Don't "
        "use to \"clear\" a file you intend to rewrite — just `write_file` over it. Returns "
        "`not_found`, `aborted_version`, or `aborted_lock` on common non-success paths."
    ),
    short_description="Remove a file or folder.",
    input_model=RemoveFileInput,
    output_model=RemoveFileOutput,
)
async def remove_file(
    path: str,
    is_folder: bool = False,
    *,
    context: ToolExecutionContextService,
) -> ToolResult:
    """Remove a file or folder."""
    resolved = normalized_path(resolve_sandbox_path(path, context))
    warnings: list[str] = []

    sandbox_id, sandbox_id_error = sandbox_id_or_error(context)
    if sandbox_id_error is not None:
        return sandbox_id_error
    api, api_error = sandbox_api_or_error(context, tool_name="remove_file")
    if api_error is not None:
        return api_error

    result = await api.remove_file(
        sandbox_id,
        RemoveFileRequest(
            path=resolved,
            is_folder=is_folder,
            actor=actor_from_context(context),
            description=f"remove {resolved}",
        ),
    )
    paths = list(result.changed_paths or (resolved,))
    if result.success:
        return ToolResult(
            output=operation_payload(
                status="deleted",
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

    payload_status = _remove_failure_status(result.conflict_reason)
    return ToolResult(
        output=operation_payload(
            status=payload_status,
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


def _remove_failure_status(conflict_reason: str | None) -> str:
    if conflict_reason in {"not_found", "missing"}:
        return "not_found"
    if conflict_reason in {"base_mismatch", "version_conflict", "drift"}:
        return "aborted_version"
    if conflict_reason in {"lock_conflict", "locked"}:
        return "aborted_lock"
    return "failed"


__all__ = ["remove_file"]
