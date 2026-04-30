"""Glob tool."""

from __future__ import annotations

from tools.core.base import ToolExecutionContextService, ToolResult
from tools.core.decorator import tool
from sandbox.daytona.exec_files import _exec_command
from sandbox.daytona.paths import (
    _get_repo_root,
    _path_error,
    _resolve_path,
)
from sandbox.daytona.recovery import _run_with_recovery
from tools.daytona_toolkit._file_tool_helpers import (
    GlobInput,
    GlobOutput,
    build_glob_result,
)
from sandbox.daytona.search_commands import build_glob_command


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
    cwd = _get_repo_root(context) or ""
    path = _resolve_path(path, context) if path != "." else (cwd or ".")
    try:
        command = build_glob_command(
            root=path,
            pattern=pattern,
        )
        resp = await _run_with_recovery(
            context,
            lambda sandbox: _exec_command(
                sandbox,
                command,
                timeout=30,
            ),
        )
        if getattr(resp, "exit_code", 0) not in (0, None):
            return ToolResult(
                output=getattr(resp, "result", "") or f"Glob search failed in {path}",
                is_error=True,
            )
        file_list = [
            f for f in (resp.result or "").splitlines() if f.strip()
        ]
        return build_glob_result(cwd=cwd, pattern=pattern, path=path, files=file_list)
    except Exception as exc:
        return ToolResult(
            output=_path_error(exc, path) or str(exc),
            is_error=True,
        )


__all__ = ["glob"]
