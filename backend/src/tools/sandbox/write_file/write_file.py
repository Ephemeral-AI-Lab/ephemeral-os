"""Write file tool."""

from __future__ import annotations

import sandbox.api as sandbox_api
from sandbox.api import WriteFileRequest
from tools._framework.core.base import ToolExecutionContextService, ToolResult
from tools._framework.core.decorator import tool
from tools.sandbox._lib.session import (
    audit_kwargs_from_context,
    caller_from_context,
    get_repo_root,
    resolve_sandbox_path,
    sandbox_audit_metadata,
    sandbox_id_or_error,
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
)
async def write_file(
    file_path: str,
    content: str,
    *,
    context: ToolExecutionContextService,
) -> ToolResult:
    """Create or overwrite a file."""
    file_path = resolve_sandbox_path(file_path, context)

    sandbox_id, sandbox_id_error = sandbox_id_or_error(context)
    if sandbox_id_error is not None:
        return sandbox_id_error

    result = await sandbox_api.write_file(
        sandbox_id,
        WriteFileRequest(
            path=file_path,
            content=content,
            caller=caller_from_context(context),
            description=f"write {file_path}",
            overwrite=True,
        ),
        **audit_kwargs_from_context(context),
    )

    paths = list(result.changed_paths or (file_path,))
    if result.success:
        return mutation_tool_result(
            success=True,
            success_status="written",
            paths=paths,
            success_extra={
                "cwd": get_repo_root(context),
                "file_path": file_path,
                "bytes_written": len(content.encode("utf-8")),
            },
            timings=result.timings,
            metadata_extra=sandbox_audit_metadata(context),
        )

    return mutation_tool_result(
        success=False,
        success_status="written",
        paths=paths,
        failure_status=result.status or None,
        conflict_reason=result.conflict_reason,
        timings=result.timings,
        metadata_extra=sandbox_audit_metadata(context),
    )


__all__ = ["write_file"]
