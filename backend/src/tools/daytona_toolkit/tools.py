"""Daytona tool implementations — @tool-decorated functions for sandbox operations."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shlex
import uuid
from typing import Any

from tools.core.decorator import tool
from tools.core.base import ToolExecutionContext, ToolResult
from tools.daytona_toolkit._daytona_utils import (
    _truncate,
    _truncate_tail,
    _format_shell_stdout,
    _wrap_bash_command,
    _extract_exit_code,
    _sandbox_context_error,
    _is_recoverable_sandbox_error,
    _attach_sandbox_to_context,
    _require_sandbox,
    _recover_sandbox,
    _path_error,
    _get_cwd,
    _resolve_path,
    _normalize_repo_relative_path,
    _normalize_string_list,
    _extract_verify_paths,
    _verification_surface_enforcement_mode,
    _normalize_write_scope,
    _path_under_write_scope,
    _team_repo_write_error,
    _team_repo_write_warning,
    _upload_file_compat,
    _DEFAULT_TIMEOUT,
    _OUTPUT_MAX_CHARS,
    _EXIT_MARKER,
    is_coordinated_team_agent,
)
from tools.core.ci_runtime import (
    abort_ci_write,
    finalize_ci_write,
    prepare_ci_write,
    prepare_declared_shell_outputs,
    release_declared_shell_outputs,
    sync_write_to_ci,
)
from tools.daytona_toolkit.ci_integration import (
    shell_mutation_declaration_error,
    sync_shell_mutations,
)

logger = logging.getLogger(__name__)


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
) -> ToolResult:
    return ToolResult(
        output=json.dumps(
            {
                "cwd": _get_cwd(context) or "",
                "file_path": file_path,
                "bytes_written": bytes_written,
                "ci_sync": True,
                "warnings": [warning] if warning else [],
            }
        )
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

# ---------------------------------------------------------------------------
# Shell execution
# ---------------------------------------------------------------------------


async def _exec_streaming(
    *,
    sandbox: Any,
    command: str,
    cwd: str | None,
    timeout: int,
    on_progress_line: Any,
) -> ToolResult:
    """Run *command* via a Daytona session and stream stdout lines live.

    Each newline-terminated chunk from stdout/stderr is forwarded to
    ``on_progress_line`` so the BackgroundTaskManager can surface a live
    tail through check_background_progress while the task is still running.
    """
    from daytona_sdk import SessionExecuteRequest

    session_id = f"bash-{uuid.uuid4().hex[:12]}"
    process = sandbox.process
    poll_interval = 0.5
    deadline = asyncio.get_event_loop().time() + timeout

    last_emitted = 0  # number of stdout chars already forwarded as progress
    line_buf = ""

    def _flush_lines(new_text: str) -> None:
        nonlocal line_buf
        if not new_text:
            return
        line_buf += new_text
        while "\n" in line_buf:
            line, line_buf = line_buf.split("\n", 1)
            if line.startswith(_EXIT_MARKER):
                continue
            try:
                on_progress_line(line)
            except Exception as cb_exc:
                logger.debug("on_progress_line callback failed: %s", cb_exc)

    try:
        await process.create_session(session_id)
    except Exception as exc:
        return ToolResult(output=f"failed to create sandbox session: {exc}", is_error=True)

    final_stdout = ""
    final_stderr = ""
    exit_code: int | None = None
    try:
        full_cmd = f"cd {shlex.quote(cwd)} && {command}" if cwd else command
        req = SessionExecuteRequest(command=full_cmd, run_async=True)
        try:
            resp = await process.execute_session_command(session_id, req)
        except Exception as exc:
            return ToolResult(output=f"failed to start command: {exc}", is_error=True)

        cmd_id = getattr(resp, "cmd_id", None) or getattr(resp, "command_id", None)
        if not cmd_id:
            return ToolResult(
                output=f"daytona session did not return a cmd_id: {resp!r}",
                is_error=True,
            )

        # Poll logs and command status until the command exits.
        while True:
            try:
                logs = await process.get_session_command_logs(session_id, cmd_id)
                stdout_text = getattr(logs, "stdout", "") or ""
                stderr_text = getattr(logs, "stderr", "") or ""
            except Exception as exc:
                logger.debug("get_session_command_logs failed: %s", exc)
                stdout_text = final_stdout
                stderr_text = final_stderr

            if len(stdout_text) > last_emitted:
                new_text = stdout_text[last_emitted:]
                last_emitted = len(stdout_text)
                _flush_lines(new_text)

            final_stdout = stdout_text
            final_stderr = stderr_text

            try:
                cmd_info = await process.get_session_command(session_id, cmd_id)
                exit_code = getattr(cmd_info, "exit_code", None)
            except Exception:
                exit_code = None

            if exit_code is not None:
                break
            if asyncio.get_event_loop().time() >= deadline:
                return ToolResult(
                    output=f"command timed out after {timeout}s",
                    is_error=True,
                    metadata={"exit_code": None},
                )
            await asyncio.sleep(poll_interval)

        # One final poll to capture any tail logs written between the last
        # poll and the exit_code becoming visible.
        try:
            logs = await process.get_session_command_logs(session_id, cmd_id)
            tail_stdout = getattr(logs, "stdout", "") or ""
            tail_stderr = getattr(logs, "stderr", "") or ""
            if len(tail_stdout) > last_emitted:
                _flush_lines(tail_stdout[last_emitted:])
            final_stdout = tail_stdout
            final_stderr = tail_stderr
        except Exception as exc:
            logger.debug("final log poll failed: %s", exc)

        if line_buf:
            if not line_buf.startswith(_EXIT_MARKER):
                try:
                    on_progress_line(line_buf)
                except Exception as cb_exc:
                    logger.debug("on_progress_line callback failed (flush): %s", cb_exc)
            line_buf = ""

        cleaned_stdout, resolved_exit_code = _extract_exit_code(
            final_stdout,
            fallback_exit_code=exit_code,
        )
        output = json.dumps(
            {
                "cwd": cwd or "",
                "stdout": _format_shell_stdout(
                    cleaned_stdout,
                    exit_code=resolved_exit_code,
                ),
                "exit_code": resolved_exit_code,
            }
        )
        return ToolResult(
            output=output,
            is_error=resolved_exit_code != 0,
            metadata={"exit_code": resolved_exit_code},
        )
    finally:
        try:
            await process.delete_session(session_id)
        except Exception as exc:
            logger.debug("failed to delete daytona session %s: %s", session_id, exc)


# ---------------------------------------------------------------------------
# File read
# ---------------------------------------------------------------------------


@tool(
    name="daytona_read_file",
    description="Read file contents, optionally specifying a line range.",
    read_only=True,
)
async def daytona_read_file(
    file_path: str,
    start_line: int = 1,
    end_line: int | None = None,
    *,
    context: ToolExecutionContext,
) -> ToolResult:
    """Read a file from the Daytona sandbox.

    Args:
        file_path: Path to the file in the sandbox
        start_line: First line to read (1-based)
        end_line: Last line to read (1-based, inclusive)

    Returns:
        file_path (str): Path to the file
        total_lines (int): Total number of lines in the file
        start_line (int): First line returned (1-based)
        end_line (int): Last line returned (1-based)
        content (str): File content with line numbers
    """
    file_path = _resolve_path(file_path, context)
    try:
        raw = await _run_with_recovery(
            context,
            lambda sandbox: sandbox.fs.download_file(file_path),
        )
        content = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
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
    description="Create a new file or overwrite an existing file with the given content.",
)
async def daytona_write_file(
    file_path: str,
    content: str,
    *,
    context: ToolExecutionContext,
) -> ToolResult:
    """Write/create a file in the Daytona sandbox.

    Args:
        file_path: Path to write in the sandbox
        content: File content to write

    Returns:
        file_path (str): Path that was written
        bytes_written (int): Number of bytes written
    """
    file_path = _resolve_path(file_path, context)
    contract_error = _team_repo_write_error(context, file_path, tool_name="daytona_write_file")
    if contract_error is not None:
        return ToolResult(output=contract_error, is_error=True)
    contract_warning = _team_repo_write_warning(context, file_path, tool_name="daytona_write_file")
    prepared = None
    sandbox = await _require_sandbox(context)

    async def _ensure_parent(active_sandbox: Any) -> None:
        parent = "/".join(file_path.split("/")[:-1])
        if parent:
            await active_sandbox.process.exec(f"mkdir -p {shlex.quote(parent)}")

    try:
        content_bytes = content.encode("utf-8")
        await _ensure_parent(sandbox)
        prepared, scope_packet, err = prepare_ci_write(
            context,
            file_path,
            allow_scope_drift=True,
        )
        if err is not None:
            return ToolResult(
                output=err,
                is_error=True,
                metadata={"scope_packet": scope_packet, "conflict": True},
            )
        if prepared is not None:
            result = finalize_ci_write(
                context,
                prepared,
                content=content,
                edit_type="write",
                description="daytona_write_file",
            )
            if not getattr(result, "success", False):
                return ToolResult(
                    output=str(getattr(result, "message", "") or "Write failed"),
                    is_error=True,
                    metadata={"conflict": bool(getattr(result, "conflict", False))},
                )
        else:
            await _upload_file_compat(sandbox, content_bytes, file_path)
            sync_write_to_ci(
                context,
                file_path,
                content,
                edit_type="write",
                description="daytona_write_file",
            )
        return _build_write_file_result(
            context=context,
            file_path=file_path,
            bytes_written=len(content_bytes),
            warning=contract_warning,
        )
    except Exception as exc:
        parent = "/".join(file_path.split("/")[:-1])
        try:
            sandbox = await _recover_sandbox(context, exc)
            content_bytes = content.encode("utf-8")
            await _ensure_parent(sandbox)
            await _upload_file_compat(sandbox, content_bytes, file_path)
            sync_write_to_ci(
                context,
                file_path,
                content,
                edit_type="write",
                description="daytona_write_file",
            )
            return _build_write_file_result(
                context=context,
                file_path=file_path,
                bytes_written=len(content_bytes),
                warning=contract_warning,
            )
        except Exception as recovery_exc:
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
    read_only=True,
)
async def daytona_grep(
    pattern: str,
    path: str = ".",
    *,
    context: ToolExecutionContext,
) -> ToolResult:
    """Search file contents in the Daytona sandbox.

    Args:
        pattern: Text pattern to search for in file contents
        path: File or directory to search

    Returns:
        pattern (str): Pattern that was searched
        path (str): Search root path
        matches (list): Matching results with file, line, content
        total_matches (int): Total matches found
    """
    cwd = _get_cwd(context) or ""
    path = _resolve_path(path, context) if path != "." else (cwd or ".")
    try:
        matches = await _run_with_recovery(
            context,
            lambda sandbox: sandbox.fs.find_files(path, pattern),
        )
        return _build_find_result(cwd=cwd, pattern=pattern, path=path, matches=matches or [])
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
    read_only=True,
)
async def daytona_glob(
    pattern: str,
    path: str = ".",
    *,
    context: ToolExecutionContext,
) -> ToolResult:
    """Find files by glob pattern in the Daytona sandbox.

    Args:
        pattern: Glob pattern to match file names (e.g. '*.py', 'test_*')
        path: Root directory to search from

    Returns:
        pattern (str): Glob pattern used
        path (str): Search root path
        files (list): Matching file paths
        total_files (int): Total files found
    """
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
