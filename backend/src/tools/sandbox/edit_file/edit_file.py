"""Edit file tool."""

from __future__ import annotations

from sandbox._shared.models import Intent

from pydantic import BaseModel, ConfigDict, Field

import sandbox.api as sandbox_api
from sandbox.api import EditFileRequest, SearchReplaceEdit
from tools._framework.core.base import ToolExecutionContextService, ToolResult
from tools._framework.core.decorator import tool
from tools.sandbox._lib.tool_context import (
    sandbox_audit_kwargs_from_tool_context,
    sandbox_caller_from_tool_context,
    sandbox_repo_root_from_tool_context,
    resolve_tool_sandbox_path,
    sandbox_audit_metadata_from_tool_context,
    sandbox_id_or_missing_error_result,
)
from tools.sandbox._lib.mutation_result import mutation_tool_result
from .prompt import get_edit_file_description


class EditFileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_path: str = Field(..., description="Repo-relative or sandbox-root file path.")
    old_text: str = Field(
        default="",
        description="Exact text to replace.",
    )
    new_text: str = Field(
        default="",
        description="Replacement text.",
    )
    description: str = Field(
        default="",
        description="Optional short note about the edit.",
    )


class EditFileOutput(BaseModel):
    cwd: str = Field(..., description="Current sandbox working directory.")
    file_path: str = Field(..., description="Resolved file path that was edited.")
    status: str = Field(..., description="Edit result: edited, aborted_version, or failed.")
    changed_paths: list[str] = Field(default_factory=list, description="Files changed by the edit.")
    changed_path_kinds: dict[str, str] = Field(
        default_factory=dict,
        description="Changed paths keyed to write/delete/symlink/opaque_dir.",
    )
    mutation_source: str = Field(default="", description="Mutation source tag.")
    conflict_reason: str | None = Field(default=None, description="Conflict reason when edit failed.")
    error: dict[str, object] = Field(default_factory=dict, description="Typed error payload.")
    applied_edits: int = Field(
        default=0,
        description="Number of replacements applied.",
    )


def _normalize_edits(
    *,
    old_text: str,
    new_text: str,
) -> tuple[list[SearchReplaceEdit], str | None]:
    """Convert tool input into one search/replace edit."""
    if not old_text:
        return [], "Provide `old_text` (text to find) and `new_text` (replacement)."
    return [SearchReplaceEdit(old_text=old_text, new_text=new_text)], None


@tool(
    name="edit_file",
    description=get_edit_file_description(),
    short_description="Apply atomic file edits.",
    input_model=EditFileInput,
    output_model=EditFileOutput,
    intent=Intent.WRITE_ALLOWED,
)
async def edit_file(
    file_path: str,
    old_text: str = "",
    new_text: str = "",
    description: str = "",
    *,
    context: ToolExecutionContextService,
) -> ToolResult:
    """Edit a file."""
    file_path = resolve_tool_sandbox_path(file_path, context)

    normalized_edits, edit_error = _normalize_edits(
        old_text=old_text,
        new_text=new_text,
    )
    if edit_error is not None:
        return ToolResult(output=edit_error, is_error=True)

    sandbox_id, sandbox_id_error = sandbox_id_or_missing_error_result(context)
    if sandbox_id_error is not None:
        return sandbox_id_error

    result = await sandbox_api.edit_file(
        sandbox_id,
        EditFileRequest(
            path=file_path,
            edits=tuple(normalized_edits),
            caller=sandbox_caller_from_tool_context(context),
            description=description or f"edit {file_path}",
        ),
        **sandbox_audit_kwargs_from_tool_context(context),
    )

    paths = list(result.changed_paths)
    if result.success:
        return mutation_tool_result(
            success=True,
            success_status="edited",
            paths=paths,
            success_extra={
                "cwd": sandbox_repo_root_from_tool_context(context),
                "file_path": file_path,
                "applied_edits": result.applied_edits,
            },
            timings=result.timings,
            mutation_source=result.mutation_source,
            changed_path_kinds=dict(result.changed_path_kinds),
            metadata_extra=sandbox_audit_metadata_from_tool_context(context),
        )

    return mutation_tool_result(
        success=False,
        success_status="edited",
        paths=paths,
        failure_status=result.status or None,
        conflict_reason=result.conflict_reason,
        error=result.error,
        mutation_source=result.mutation_source,
        changed_path_kinds=dict(result.changed_path_kinds),
        timings=result.timings,
        metadata_extra=sandbox_audit_metadata_from_tool_context(context),
    )


__all__ = ["edit_file"]
