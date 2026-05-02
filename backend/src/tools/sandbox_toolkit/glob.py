"""Glob tool."""

from __future__ import annotations

from sandbox.api.models import GlobRequest
from tools.core.base import ToolExecutionContextService, ToolResult
from tools.core.decorator import tool
from tools.core.sandbox_session import (
    actor_from_context,
    get_repo_root,
    path_error,
    resolve_sandbox_path,
    sandbox_api_or_error,
    sandbox_id_or_error,
)
from tools.sandbox_toolkit._file_tool_helpers import (
    GlobInput,
    GlobOutput,
    build_glob_result,
)


@tool(
    name="glob",
    description=(
        "Find files by name pattern (e.g. `**/*.py`, `test_*.py`). Returns matching paths only "
        "— never reads contents. Use to enumerate files of a type, scope a follow-up grep, or "
        "check existence. Prefer over `shell` find/ls. Use `grep` instead when you care about "
        "file contents."
    ),
    short_description="Find files by glob.",
    input_model=GlobInput,
    output_model=GlobOutput,
)
async def glob(
    pattern: str,
    path: str = ".",
    *,
    context: ToolExecutionContextService,
) -> ToolResult:
    """Find files by glob pattern."""
    cwd = get_repo_root(context)
    path = resolve_sandbox_path(path, context) if path != "." else (cwd or ".")
    sandbox_id, sandbox_id_error = sandbox_id_or_error(context)
    if sandbox_id_error is not None:
        return sandbox_id_error
    api, api_error = sandbox_api_or_error(context, tool_name="glob")
    if api_error is not None:
        return api_error
    try:
        result = await api.glob(
            sandbox_id,
            GlobRequest(
                pattern=pattern,
                path=path,
                timeout=30,
                actor=actor_from_context(context),
            ),
        )
        file_list = list(result.files)
        return build_glob_result(cwd=cwd, pattern=pattern, path=path, files=file_list)
    except Exception as exc:
        return ToolResult(
            output=path_error(exc, path) or str(exc),
            is_error=True,
        )


__all__ = ["glob"]
