"""Read file tool."""

from __future__ import annotations

from tools.core.base import ToolExecutionContextService, ToolResult
from tools.core.decorator import tool
from sandbox.daytona_utils import (
    _path_error,
    _read_text_file_via_exec,
    _resolve_path,
    _run_with_recovery,
)
from tools.daytona_toolkit._file_tool_helpers import (
    MAX_READ_FILE_LINES,
    ReadFileInput,
    ReadFileOutput,
    build_read_file_result,
)


@tool(
    name="read_file",
    description=(
        "Read a UTF-8 text file from the sandbox, optionally restricted to a line range. "
        "Each call can return at most 200 lines. Output is line-numbered for easy citation. "
        "Prefer this over `shell` with cat/sed/head — cheaper and structured. Don't use on "
        "binary files or for directory listings (use `glob`). Paths are repo-relative or "
        "sandbox-absolute."
    ),
    short_description="Read a file from the sandbox.",
    input_model=ReadFileInput,
    output_model=ReadFileOutput,
)
async def read_file(
    file_path: str,
    start_line: int = 1,
    end_line: int = MAX_READ_FILE_LINES,
    *,
    context: ToolExecutionContextService,
) -> ToolResult:
    """Read a file."""
    file_path = _resolve_path(file_path, context)
    try:
        content, _ = await _run_with_recovery(
            context,
            lambda sandbox: _read_text_file_via_exec(sandbox, file_path),
        )
        return build_read_file_result(
            context=context,
            file_path=file_path,
            content=content,
            start_line=start_line,
            end_line=end_line,
        )
    except Exception as exc:
        return ToolResult(
            output=_path_error(exc, file_path) or str(exc),
            is_error=True,
        )


__all__ = ["read_file"]
