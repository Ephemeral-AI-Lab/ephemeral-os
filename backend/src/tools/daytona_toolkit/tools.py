"""Daytona tool implementations — @tool-decorated functions for sandbox operations."""

from __future__ import annotations

import json
import logging
import shlex
from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel, Field

from tools.core.decorator import tool
from tools.core.base import ToolExecutionContext, ToolResult
from tools.daytona_toolkit._daytona_utils import (
    _truncate,
    _require_sandbox,
    _recover_sandbox,
    _path_error,
    _get_cwd,
    _extract_exit_code,
    _read_text_file_via_exec,
    _resolve_path,
    _normalize_repo_relative_path,
    _normalize_string_list,
    _team_repo_write_error,
    _team_repo_write_warning,
    _wrap_bash_command,
    is_coordinated_team_agent,
    record_coordination_warning,
)
from tools.core.ci_runtime import (
    abort_ci_write,
    finalize_ci_write,
    prepare_ci_write,
)

logger = logging.getLogger(__name__)


class DaytonaReadFileInput(BaseModel):
    file_path: str = Field(..., description="Path to the file in the sandbox.")
    start_line: int = Field(
        default=1,
        ge=1,
        description="First line to read, using one-based numbering.",
    )
    end_line: int | None = Field(
        default=None,
        ge=1,
        description="Last line to read, using one-based inclusive numbering.",
    )


class DaytonaReadFileOutput(BaseModel):
    cwd: str = Field(..., description="Current sandbox working directory.")
    file_path: str = Field(..., description="Resolved file path that was read.")
    total_lines: int = Field(..., description="Total number of lines in the file.")
    start_line: int = Field(..., description="First line returned.")
    end_line: int = Field(..., description="Last line returned.")
    content: str = Field(..., description="Selected file content with line numbers.")


class DaytonaWriteFileInput(BaseModel):
    file_path: str = Field(..., description="Path to create or overwrite in the sandbox.")
    content: str = Field(..., description="UTF-8 text content to write.")


class DaytonaWriteFileOutput(BaseModel):
    cwd: str = Field(..., description="Current sandbox working directory.")
    file_path: str = Field(..., description="Resolved file path that was written.")
    bytes_written: int = Field(..., description="Number of UTF-8 bytes written.")
    ci_sync: bool = Field(..., description="Whether the write was synchronized to code intelligence.")
    warnings: list[str] = Field(default_factory=list, description="Non-fatal write warnings.")
    timings: dict[str, Any] | None = Field(
        default=None,
        description="Optional write timing metadata.",
    )


class DaytonaGrepInput(BaseModel):
    pattern: str = Field(..., description="Text pattern to search for in file contents.")
    path: str = Field(
        default=".",
        description="File or directory path to search.",
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


class DaytonaGlobInput(BaseModel):
    pattern: str = Field(..., description="Glob pattern to match file names.")
    path: str = Field(
        default=".",
        description="Directory path to search from.",
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


def _build_match_result(match: Any) -> dict[str, Any]:
    if isinstance(match, dict):
        return {
            "file": str(match.get("file") or ""),
            "line": match.get("line"),
            "content": str(match.get("content") or "").rstrip(),
        }
    return {
        "file": getattr(match, "file", None) or "",
        "line": getattr(match, "line", None),
        "content": (getattr(match, "content", None) or "").rstrip(),
    }


def _build_write_file_result(
    *,
    context: ToolExecutionContext,
    file_path: str,
    bytes_written: int,
    warning: str | None,
    timings: dict[str, Any] | None = None,
) -> ToolResult:
    normalized_timings = timings if isinstance(timings, dict) else None
    payload = {
        "cwd": _get_cwd(context) or "",
        "file_path": file_path,
        "bytes_written": bytes_written,
        "ci_sync": True,
        "warnings": [warning] if warning else [],
    }
    if normalized_timings:
        payload["timings"] = normalized_timings
    return ToolResult(
        output=json.dumps(payload),
        metadata={"timings": dict(normalized_timings or {})},
    )


def _build_find_result(
    *,
    cwd: str,
    pattern: str,
    path: str,
    matches: list[Any],
) -> ToolResult:
    return ToolResult(
        output=json.dumps(
            {
                "cwd": cwd,
                "pattern": pattern,
                "path": path,
                "matches": [_build_match_result(match) for match in matches[:500]],
                "total_matches": len(matches),
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


def _benchmark_read_guard(context: ToolExecutionContext, file_path: str) -> str | None:
    if not is_coordinated_team_agent(context):
        return None
    repo_root = str(_get_cwd(context) or "").strip()
    benchmark_files = _normalize_string_list(
        context.metadata.get("benchmark_test_files"),
        repo_root,
    )
    if not benchmark_files and not context.metadata.get("benchmark_test_ids"):
        return None
    rel_path = _normalize_repo_relative_path(file_path, repo_root) or ""
    if rel_path and rel_path in benchmark_files:
        return (
            "Benchmark read guard: do not open benchmark test files with "
            "`daytona_read_file(...)` on coordinated lanes. Use the named pytest "
            "ids, scout notes, and runtime traceback instead."
        )
    if int(context.metadata.get("_daytona_codeact_calls") or 0) <= 0:
        return (
            "Benchmark read guard: on coordinated benchmark lanes, run the exact "
            "repro first via `daytona_codeact` and direct `shell(\"pytest ...\", "
            "timeout=N)` before using `daytona_read_file(...)`."
        )
    return None


def _build_glob_command(*, root: str, pattern: str) -> str:
    patterns = [pattern]
    if pattern.startswith("**/"):
        patterns.append(pattern[3:])
    payload = json.dumps(list(dict.fromkeys(p for p in patterns if p)))
    script = """
import fnmatch
import json
import os
import sys

root = sys.argv[1]
patterns = json.loads(sys.argv[2])
matches = []

for dirpath, _, filenames in os.walk(root):
    for filename in filenames:
        full_path = os.path.join(dirpath, filename)
        rel_path = os.path.relpath(full_path, root).replace(os.sep, "/")
        if any(
            fnmatch.fnmatch(rel_path, pattern) or fnmatch.fnmatch(filename, pattern)
            for pattern in patterns
        ):
            matches.append(full_path)
            if len(matches) >= 500:
                break
    if len(matches) >= 500:
        break

print("\\n".join(matches))
"""
    return f"python3 -c {shlex.quote(script)} {shlex.quote(root)} {shlex.quote(payload)}"


def _build_grep_command(*, root: str, pattern: str) -> str:
    script = r"""
import json
import pathlib
import re
import sys

pattern = sys.argv[1]
root = pathlib.Path(sys.argv[2])

try:
    regex = re.compile(pattern)
except re.error as exc:
    print(json.dumps({"ok": False, "error": f"Invalid regex: {exc}"}))
    sys.exit(2)

if not root.exists():
    print(json.dumps({"ok": False, "error": f"Path does not exist: {root}"}))
    sys.exit(1)

paths = [root] if root.is_file() else [p for p in root.rglob("*") if p.is_file()]
matches = []
total = 0
for path in paths:
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for line_no, line in enumerate(handle, start=1):
                if regex.search(line):
                    total += 1
                    if len(matches) < 500:
                        matches.append({
                            "file": str(path),
                            "line": line_no,
                            "content": line.rstrip("\n"),
                        })
    except OSError:
        continue

print(json.dumps({"ok": True, "matches": matches, "total_matches": total}))
"""
    return (
        f"python3 -c {shlex.quote(script)} "
        f"{shlex.quote(pattern)} {shlex.quote(root)}"
    )


# ---------------------------------------------------------------------------
# Shell execution
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# File read
# ---------------------------------------------------------------------------


@tool(
    name="daytona_read_file",
    description=(
        "Read file contents, optionally specifying a line range. On coordinated "
        "benchmark lanes, run the exact runtime repro first and do not use this "
        "to open benchmark test files."
    ),
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
    """Read a file from the Daytona sandbox."""
    file_path = _resolve_path(file_path, context)
    contract_error = _benchmark_read_guard(context, file_path)
    if contract_error is not None:
        return ToolResult(output=contract_error, is_error=True)
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
    description=(
        "Create a new file or overwrite an existing file with the given content. "
        "Use the exact tool name `daytona_write_file`; there is no `write_file` tool."
    ),
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
    """Write/create a file in the Daytona sandbox."""
    file_path = _resolve_path(file_path, context)
    contract_error = _team_repo_write_error(context, file_path, tool_name="daytona_write_file")
    if contract_error is not None:
        return ToolResult(output=contract_error, is_error=True)
    contract_warning = _team_repo_write_warning(context, file_path, tool_name="daytona_write_file")
    if contract_warning is not None:
        record_coordination_warning(
            context,
            category="write_scope",
            message=contract_warning,
        )
    prepared = None
    content_bytes = content.encode("utf-8")

    async def _attempt(_active_sandbox: Any) -> ToolResult:
        nonlocal prepared
        prepared, scope_packet, err = prepare_ci_write(
            context, file_path, allow_scope_drift=True,
        )
        if err is not None:
            return ToolResult(
                output=err, is_error=True,
                metadata={"scope_packet": scope_packet, "conflict": True},
            )
        if prepared is not None:
            result = finalize_ci_write(
                context, prepared, content=content,
                edit_type="write", description="daytona_write_file",
            )
            if not getattr(result, "success", False):
                return ToolResult(
                    output=str(getattr(result, "message", "") or "Write failed"),
                    is_error=True,
                    metadata={"conflict": bool(getattr(result, "conflict", False))},
                )
        else:
            return ToolResult(
                output=(
                    f"daytona_write_file: Code intelligence/OCC is unavailable for write of "
                    f"{file_path}. Direct sandbox write fallback is disabled."
                ),
                is_error=True,
                metadata={"occ_required": True},
            )
        return _build_write_file_result(
            context=context, file_path=file_path,
            bytes_written=len(content_bytes), warning=contract_warning,
            timings=getattr(result, "timings", None) if prepared is not None else None,
        )

    try:
        sandbox = await _require_sandbox(context)
        return await _attempt(sandbox)
    except Exception as exc:
        try:
            sandbox = await _recover_sandbox(context, exc)
            return await _attempt(sandbox)
        except Exception as recovery_exc:
            parent = "/".join(file_path.split("/")[:-1])
            return ToolResult(
                output=_path_error(recovery_exc, parent) or str(recovery_exc),
                is_error=True,
            )
    finally:
        abort_ci_write(context, prepared)


# ---------------------------------------------------------------------------
# Grep search
# ---------------------------------------------------------------------------


@tool(
    name="daytona_grep",
    description="Search file contents for a text pattern and return matching lines.",
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
    """Search file contents in the Daytona sandbox."""
    cwd = _get_cwd(context) or ""
    path = _resolve_path(path, context) if path != "." else (cwd or ".")
    try:
        command = _wrap_bash_command(_build_grep_command(root=path, pattern=pattern))
        response = await _run_with_recovery(
            context,
            lambda sandbox: sandbox.process.exec(
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
            SimpleNamespace(
                file=str(item.get("file") or ""),
                line=item.get("line"),
                content=str(item.get("content") or ""),
            )
            for item in raw_matches
            if isinstance(item, dict)
        ]
        return _build_find_result(cwd=cwd, pattern=pattern, path=path, matches=matches)
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
    description="Find files by name using a glob pattern (e.g. '*.py', 'test_*').",
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
    """Find files by glob pattern in the Daytona sandbox."""
    cwd = _get_cwd(context) or ""
    path = _resolve_path(path, context) if path != "." else (cwd or ".")
    try:
        command = _build_glob_command(root=path, pattern=pattern)
        resp = await _run_with_recovery(
            context,
            lambda sandbox: sandbox.process.exec(
                command,
                timeout=30,
            ),
        )
        if getattr(resp, "exit_code", 0) not in (0, None):
            return ToolResult(
                output=getattr(resp, "result", "") or f"Glob search failed in {path}",
                is_error=True,
            )
        file_list = [f for f in (resp.result or "").splitlines() if f.strip()][:500]
        return _build_glob_result(cwd=cwd, pattern=pattern, path=path, files=file_list)
    except Exception as exc:
        return ToolResult(
            output=_path_error(exc, path) or str(exc),
            is_error=True,
        )
