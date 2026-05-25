"""Write file tool."""

from __future__ import annotations

import sandbox.api as sandbox_api
from sandbox._shared.models import Intent
from sandbox.api import WriteFileRequest
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
from tools.sandbox._lib.file_payloads import (
    WriteFileInput,
    WriteFileOutput,
)
from tools.sandbox._lib.mutation_result import mutation_tool_result
from .prompt import get_write_file_description


@tool(
    name="write_file",
    description=get_write_file_description(),
    short_description="Create or overwrite a file.",
    input_model=WriteFileInput,
    output_model=WriteFileOutput,
    intent=Intent.WRITE_ALLOWED,
)
async def write_file(
    file_path: str,
    content: str,
    *,
    context: ToolExecutionContextService,
) -> ToolResult:
    """Create or overwrite a file."""
    file_path = resolve_tool_sandbox_path(file_path, context)

    sandbox_id, sandbox_id_error = sandbox_id_or_missing_error_result(context)
    if sandbox_id_error is not None:
        return sandbox_id_error

    result = await sandbox_api.write_file(
        sandbox_id,
        WriteFileRequest(
            path=file_path,
            content=content,
            caller=sandbox_caller_from_tool_context(context),
            description=f"write {file_path}",
            overwrite=True,
        ),
        **sandbox_audit_kwargs_from_tool_context(context),
    )

    paths = list(result.changed_paths)
    if result.success:
        return mutation_tool_result(
            success=True,
            success_status="written",
            paths=paths,
            success_extra={
                "cwd": sandbox_repo_root_from_tool_context(context),
                "file_path": file_path,
                "bytes_written": len(content.encode("utf-8")),
            },
            timings=result.timings,
            mutation_source=result.mutation_source,
            changed_path_kinds=dict(result.changed_path_kinds),
            metadata_extra=sandbox_audit_metadata_from_tool_context(context),
        )

    return mutation_tool_result(
        success=False,
        success_status="written",
        paths=paths,
        failure_status=result.status or None,
        conflict_reason=result.conflict_reason,
        error=result.error,
        mutation_source=result.mutation_source,
        changed_path_kinds=dict(result.changed_path_kinds),
        timings=result.timings,
        metadata_extra=sandbox_audit_metadata_from_tool_context(context),
    )


__all__ = ["write_file"]
