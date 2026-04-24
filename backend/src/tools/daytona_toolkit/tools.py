"""Daytona file tools."""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, Field

from code_intelligence.tuning import CODE_INTELLIGENCE_TUNING
from code_intelligence.types import WriteSpec
from tools.core.base import ToolExecutionContext, ToolResult
from tools.core.ci_runtime import ci_write_required_result, get_ci_service
from tools.core.decorator import tool
from tools.core.op_result_to_tool_result import operation_result_to_tool_result
from tools.daytona_toolkit._commit import submit_commit
from tools.daytona_toolkit._daytona_utils import (
    _exec_command,
    _extract_exit_code,
    _get_cwd,
    _path_error,
    _read_text_file_via_exec,
    _recover_sandbox,
    _require_sandbox,
    _resolve_path,
    _truncate,
    _wrap_bash_command,
)
from tools.daytona_toolkit.search_commands import (
    build_glob_command,
    build_grep_command,
)

logger = logging.getLogger(__name__)
_GREP_MATCH_CAP = CODE_INTELLIGENCE_TUNING.grep_match_cap

class DaytonaReadFileInput(BaseModel):
    file_path: str = Field(
        ...,
        description="Repo-relative or sandbox-root file path.",
    )
    start_line: int = Field(
        default=1,
        ge=1,
        description="First line to return. Lines are 1-based.",
    )
    end_line: int | None = Field(
        default=None,
        ge=1,
        description="Last line to return. The line is included.",
    )


class DaytonaReadFileOutput(BaseModel):
    cwd: str = Field(..., description="Current sandbox working directory.")
    file_path: str = Field(..., description="Resolved file path that was read.")
    total_lines: int = Field(..., description="Total number of lines in the file.")
    start_line: int = Field(..., description="First line returned.")
    end_line: int = Field(..., description="Last line returned.")
    content: str = Field(..., description="Selected file content with line numbers.")


class DaytonaWriteFileInput(BaseModel):
    file_path: str = Field(
        ...,
        description="Repo-relative or sandbox-root file path.",
    )
    content: str = Field(..., description="Text to write.")


class DaytonaWriteFileOutput(BaseModel):
    cwd: str = Field(..., description="Current sandbox working directory.")
    file_path: str = Field(..., description="Resolved file path that was written.")
    bytes_written: int = Field(..., description="Number of UTF-8 bytes written.")
    ci_sync: bool = Field(..., description="Whether code intelligence saw the write.")
    warnings: list[str] = Field(default_factory=list, description="Non-fatal write warnings.")
    timings: dict[str, Any] | None = Field(
        default=None,
        description="Optional write timing metadata.",
    )


class DaytonaGrepInput(BaseModel):
    pattern: str = Field(..., description="Regex pattern to search for in file contents.")
    path: str = Field(
        default=".",
        description="Repo-relative or sandbox-root directory path.",
    )


class DaytonaMatchOutput(BaseModel):
    file: str = Field(..., description="Matched file path.")
    line: int | None = Field(default=None, description="Matched one-based line number.")
    content: str = Field(..., description="Matched line content.")


class DaytonaGrepOutput(BaseModel):
    cwd: str = Field(..., description="Current sandbox working directory.")
    pattern: str = Field(..., description="Pattern that was searched.")
    path: str = Field(..., description="Search root path.")
    matches: list[DaytonaMatchOutput] = Field(
        default_factory=list,
        description="Matching file lines.",
    )
    total_matches: int = Field(..., description="Total number of matches found.")
    truncated: bool = Field(..., description="Whether returned matches were capped.")


class DaytonaGlobInput(BaseModel):
    pattern: str = Field(..., description="Glob pattern for file names, such as **/*.py.")
    path: str = Field(
        default=".",
        description="Repo-relative or sandbox-root directory path.",
    )


class DaytonaGlobOutput(BaseModel):
    cwd: str = Field(..., description="Current sandbox working directory.")
    pattern: str = Field(..., description="Glob pattern used.")
    path: str = Field(..., description="Search root path.")
    files: list[str] = Field(default_factory=list, description="Matching file paths.")
    total_files: int = Field(..., description="Total number of matching files.")


async def _run_with_recovery(
    context: ToolExecutionContext,
    operation: Any,
) -> Any:
    """Run a sandbox operation once, then retry after sandbox recovery."""
    sandbox = await _require_sandbox(context)
    try:
        return await operation(sandbox)
    except Exception as exc:
        return await operation(await _recover_sandbox(context, exc))


def _build_read_file_result(
    *,
    context: ToolExecutionContext,
    file_path: str,
    content: str,
    start_line: int,
    end_line: int | None,
) -> ToolResult:
    lines = content.splitlines()
    total = len(lines)
    start = max(1, start_line)
    end = min(total, end_line) if end_line else total
    selected = [f"{i:4d}: {lines[i - 1]}" for i in range(start, end + 1)]
    return ToolResult(
        output=json.dumps(
            {
                "cwd": _get_cwd(context) or "",
                "file_path": file_path,
                "total_lines": total,
                "start_line": start,
                "end_line": end,
                "content": _truncate("\n".join(selected)),
            }
        )
    )


def _build_match_result(match: dict[str, Any]) -> dict[str, Any]:
    return {
        "file": str(match.get("file") or ""),
        "line": match.get("line"),
        "content": str(match.get("content") or "").rstrip(),
    }


def _build_find_result(
    *,
    cwd: str,
    pattern: str,
    path: str,
    matches: list[dict[str, Any]],
    total_matches: int | None = None,
    truncated: bool = False,
) -> ToolResult:
    total = len(matches) if total_matches is None else int(total_matches)
    return ToolResult(
        output=json.dumps(
            {
                "cwd": cwd,
                "pattern": pattern,
                "path": path,
                "matches": [_build_match_result(match) for match in matches[:_GREP_MATCH_CAP]],
                "total_matches": total,
                "truncated": bool(truncated or total > _GREP_MATCH_CAP),
            }
        )
    )


def _build_glob_result(
    *,
    cwd: str,
    pattern: str,
    path: str,
    files: list[str],
) -> ToolResult:
    return ToolResult(
        output=json.dumps(
            {
                "cwd": cwd,
                "pattern": pattern,
                "path": path,
                "files": files,
                "total_files": len(files),
            }
        )
    )


# ---------------------------------------------------------------------------
# Shell execution
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# File read
# ---------------------------------------------------------------------------


@tool(
    name="daytona_read_file",
    description="Read a sandbox file.",
    short_description="Read a file from the sandbox.",
    input_model=DaytonaReadFileInput,
    output_model=DaytonaReadFileOutput,
)
async def daytona_read_file(
    file_path: str,
    start_line: int = 1,
    end_line: int | None = None,
    *,
    context: ToolExecutionContext,
) -> ToolResult:
    file_path = _resolve_path(file_path, context)
    try:
        sandbox = await _require_sandbox(context)
        try:
            content, _ = await _read_text_file_via_exec(sandbox, file_path)
        except Exception as exc:
            sandbox = await _recover_sandbox(context, exc)
            content, _ = await _read_text_file_via_exec(sandbox, file_path)
        return _build_read_file_result(
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


# ---------------------------------------------------------------------------
# File write
# ---------------------------------------------------------------------------


@tool(
    name="daytona_write_file",
    description="Create or overwrite a sandbox file.",
    short_description="Create or overwrite a file.",
    input_model=DaytonaWriteFileInput,
    output_model=DaytonaWriteFileOutput,
)
async def daytona_write_file(
    file_path: str,
    content: str,
    *,
    context: ToolExecutionContext,
) -> ToolResult:
    file_path = _resolve_path(file_path, context)
    warnings: list[str] = []

    if get_ci_service(context) is None:
        return ci_write_required_result("daytona_write_file", file_path)

    change = await submit_commit(
        context,
        op="write",
        specs=[WriteSpec(file_path=file_path, content=content, overwrite=True)],
        fallback_paths=[file_path],
        description=f"write {file_path}",
    )

    return operation_result_to_tool_result(
        change.raw,
        tool_name="daytona_write_file",
        success_status="written",
        primary_paths=[file_path],
        warnings=warnings,
        success_extra={
            "cwd": _get_cwd(context) or "",
            "file_path": file_path,
            "bytes_written": len(content.encode("utf-8")),
            "ci_sync": True,
        },
        metadata_extra={
            "changed_paths": list(change.changed_paths),
            "ambient_changed_paths": list(change.ambient_changed_paths),
            "conflict_reason": change.conflict_reason,
        },
    )


# ---------------------------------------------------------------------------
# Grep search
# ---------------------------------------------------------------------------


@tool(
    name="daytona_grep",
    description="Search file contents with a regex and return matching lines.",
    short_description="Search file contents by pattern.",
    input_model=DaytonaGrepInput,
    output_model=DaytonaGrepOutput,
)
async def daytona_grep(
    pattern: str,
    path: str = ".",
    *,
    context: ToolExecutionContext,
) -> ToolResult:
    cwd = _get_cwd(context) or ""
    path = _resolve_path(path, context) if path != "." else (cwd or ".")
    try:
        command = _wrap_bash_command(
            build_grep_command(root=path, pattern=pattern, match_cap=_GREP_MATCH_CAP)
        )
        response = await _run_with_recovery(
            context,
            lambda sandbox: _exec_command(
                sandbox,
                command,
                timeout=60,
            ),
        )
        stdout = getattr(response, "result", "") or ""
        cleaned, exit_code = _extract_exit_code(
            stdout,
            fallback_exit_code=getattr(response, "exit_code", None),
        )
        payload = json.loads(cleaned or "{}")
        if exit_code not in (0, None) or not bool(payload.get("ok", False)):
            return ToolResult(
                output=str(payload.get("error") or cleaned or f"Search failed in {path}"),
                is_error=True,
            )
        raw_matches = payload.get("matches") or []
        matches = [
            item
            for item in raw_matches
            if isinstance(item, dict)
        ]
        return _build_find_result(
            cwd=cwd,
            pattern=pattern,
            path=path,
            matches=matches,
            total_matches=payload.get("total_matches"),
            truncated=bool(payload.get("truncated", False)),
        )
    except Exception as exc:
        return ToolResult(
            output=_path_error(exc, path) or str(exc),
            is_error=True,
        )


# ---------------------------------------------------------------------------
# Glob search
# ---------------------------------------------------------------------------


@tool(
    name="daytona_glob",
    description="Find files by glob pattern, such as **/*.py or test_*.py.",
    short_description="Find files by glob.",
    input_model=DaytonaGlobInput,
    output_model=DaytonaGlobOutput,
)
async def daytona_glob(
    pattern: str,
    path: str = ".",
    *,
    context: ToolExecutionContext,
) -> ToolResult:
    cwd = _get_cwd(context) or ""
    path = _resolve_path(path, context) if path != "." else (cwd or ".")
    try:
        command = build_glob_command(
            root=path,
            pattern=pattern,
            match_cap=_GREP_MATCH_CAP,
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
        ][: int(_GREP_MATCH_CAP)]
        return _build_glob_result(cwd=cwd, pattern=pattern, path=path, files=file_list)
    except Exception as exc:
        return ToolResult(
            output=_path_error(exc, path) or str(exc),
            is_error=True,
        )
