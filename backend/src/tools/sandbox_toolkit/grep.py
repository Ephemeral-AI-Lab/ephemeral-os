"""Grep tool."""

from __future__ import annotations

from sandbox.api.models import GrepRequest
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
    GrepInput,
    GrepOutput,
    build_find_result,
)


@tool(
    name="grep",
    description=(
        "Search file contents with a regex. Returns structured {file, line, content} matches. "
        "Use to locate symbols, callers, or strings before editing. Prefer over `shell` grep/rg "
        "— no escape pitfalls. Case-sensitive by default; prefix `(?i)` for insensitive. Combine "
        "with `glob` to scope by extension. Doesn't match filenames."
    ),
    short_description="Search file contents by pattern.",
    input_model=GrepInput,
    output_model=GrepOutput,
)
async def grep(
    pattern: str,
    path: str = ".",
    *,
    context: ToolExecutionContextService,
) -> ToolResult:
    """Search file contents with a regex."""
    cwd = get_repo_root(context)
    path = resolve_sandbox_path(path, context) if path != "." else (cwd or ".")
    sandbox_id, sandbox_id_error = sandbox_id_or_error(context)
    if sandbox_id_error is not None:
        return sandbox_id_error
    api, api_error = sandbox_api_or_error(context, tool_name="grep")
    if api_error is not None:
        return api_error
    try:
        result = await api.grep(
            sandbox_id,
            GrepRequest(
                pattern=pattern,
                path=path,
                timeout=60,
                actor=actor_from_context(context),
            ),
        )
        matches = [
            {
                "file": match.file_path,
                "line": match.line,
                "content": match.text,
            }
            for match in result.matches
        ]
        return build_find_result(
            cwd=cwd,
            pattern=pattern,
            path=path,
            matches=matches,
            total_matches=result.total_matches,
        )
    except Exception as exc:
        return ToolResult(
            output=path_error(exc, path) or str(exc),
            is_error=True,
        )


__all__ = ["grep"]
