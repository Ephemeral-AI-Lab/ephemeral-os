"""Daytona tool implementations — @tool-decorated functions for sandbox operations."""

from __future__ import annotations

import asyncio
import json
import logging
import shlex
import uuid
from typing import Any

from tools.core.base import ToolExecutionContext, ToolResult
from tools.core.decorator import tool

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_DEFAULT_TIMEOUT = 120
_OUTPUT_MAX_CHARS = 8000


def _truncate(text: str, max_chars: int = _OUTPUT_MAX_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return text[:half] + f"\n\n... truncated ({len(text)} chars total) ...\n\n" + text[-half:]


def _get_sandbox(context: ToolExecutionContext) -> Any:
    """Retrieve the sandbox object from tool execution context metadata."""
    sandbox = context.metadata.get("daytona_sandbox")
    if sandbox is None:
        raise RuntimeError(
            "No Daytona sandbox in context. "
            "Ensure DaytonaToolkit was initialized with a valid sandbox_id."
        )
    return sandbox


def _path_error(exc: Exception, path: str) -> str | None:
    """Return a human-readable message if *exc* is a path-not-found error, else None."""
    msg = str(exc)
    if isinstance(exc, FileNotFoundError) or "No such file or directory" in msg:
        return f"Path does not exist: {path}"
    # Daytona SDK wraps errors and may lose the inner message
    _sdk_prefixes = ("Failed to list files", "Failed to upload files", "Failed to download")
    if any(msg.startswith(p) for p in _sdk_prefixes) and msg.rstrip().endswith(":"):
        return f"Path does not exist: {path}"
    return None


def _get_cwd(context: ToolExecutionContext) -> str | None:
    """Get working directory, preferring sandbox project dir.

    Returns None if no sandbox-specific cwd is set, letting the sandbox
    use its default directory (typically /home/daytona).
    """
    return context.metadata.get("daytona_cwd")


def _resolve_path(path: str, context: ToolExecutionContext) -> str:
    """Resolve a relative path against the sandbox cwd.

    Absolute paths are returned as-is. Relative paths are joined
    with the sandbox cwd (detected via pwd on first connect).
    """
    if path.startswith("/"):
        return path
    cwd = _get_cwd(context)
    if cwd:
        return f"{cwd}/{path}"
    return path


# ---------------------------------------------------------------------------
# Shell execution
# ---------------------------------------------------------------------------


@tool(name="daytona_bash", description="Run a shell command and return stdout and exit code.", background="optional")
async def daytona_bash(
    command: str,
    timeout: int = _DEFAULT_TIMEOUT,
    *,
    context: ToolExecutionContext,
) -> ToolResult:
    """Execute a shell command in a Daytona sandbox.

    Args:
        command: Shell command to execute in the sandbox
        timeout: Timeout in seconds

    Returns:
        stdout (str): Standard output from the command
        exit_code (int): Exit code (0 = success)
    """
    sandbox = _get_sandbox(context)
    cwd = _get_cwd(context)
    on_progress_line = context.metadata.get("on_progress_line")

    wrapped = f"env -u LC_ALL bash -c {shlex.quote(command)}"

    # Streaming path: when launched as a background task, query.py injects
    # ``on_progress_line`` into the metadata. Use a Daytona session so we can
    # tail stdout/stderr live and feed each line into the BackgroundTaskManager,
    # making the partial output visible via check_background_progress mid-run.
    if callable(on_progress_line):
        return await _exec_streaming(
            sandbox=sandbox,
            command=wrapped,
            cwd=cwd,
            timeout=timeout,
            on_progress_line=on_progress_line,
        )

    try:
        kwargs: dict[str, object] = {"timeout": timeout}
        if cwd:
            kwargs["cwd"] = cwd
        response = await sandbox.process.exec(wrapped, **kwargs)
        exit_code = getattr(response, "exit_code", 0)
        output = json.dumps(
            {
                "cwd": cwd or "",
                "stdout": _truncate(response.result or ""),
                "exit_code": exit_code,
            }
        )
        return ToolResult(
            output=output,
            is_error=exit_code != 0,
            metadata={"exit_code": exit_code},
        )
    except Exception as exc:
        return ToolResult(output=str(exc), is_error=True)


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
            try:
                on_progress_line(line_buf)
            except Exception as cb_exc:
                logger.debug("on_progress_line callback failed (flush): %s", cb_exc)
            line_buf = ""

        combined = final_stdout + (("\n" + final_stderr) if final_stderr else "")
        output = json.dumps(
            {
                "cwd": cwd or "",
                "stdout": _truncate(combined),
                "exit_code": exit_code if exit_code is not None else 0,
            }
        )
        return ToolResult(
            output=output,
            is_error=bool(exit_code) if exit_code is not None else False,
            metadata={"exit_code": exit_code},
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
    sandbox = _get_sandbox(context)
    file_path = _resolve_path(file_path, context)
    try:
        raw = await sandbox.fs.download_file(file_path)
        content = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        lines = content.splitlines()
        total = len(lines)

        start = max(1, start_line)
        end = min(total, end_line) if end_line else total

        selected = []
        for i in range(start, end + 1):
            selected.append(f"{i:4d}: {lines[i - 1]}")

        output = json.dumps(
            {
                "cwd": _get_cwd(context) or "",
                "file_path": file_path,
                "total_lines": total,
                "start_line": start,
                "end_line": end,
                "content": _truncate("\n".join(selected)),
            }
        )
        return ToolResult(output=output)
    except Exception as exc:
        return ToolResult(output=_path_error(exc, file_path) or str(exc), is_error=True)


# ---------------------------------------------------------------------------
# File write
# ---------------------------------------------------------------------------


@tool(
    name="daytona_write_file", description="Create a new file or overwrite an existing file with the given content."
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
    sandbox = _get_sandbox(context)
    file_path = _resolve_path(file_path, context)
    try:
        content_bytes = content.encode("utf-8")
        # Ensure parent directories exist
        parent = "/".join(file_path.split("/")[:-1])
        if parent:
            await sandbox.process.exec(f"mkdir -p {shlex.quote(parent)}")
        # SDK signature: upload_file(src: str | bytes, dst: str)
        await sandbox.fs.upload_file(content_bytes, file_path)
        output = json.dumps(
            {
                "cwd": _get_cwd(context) or "",
                "file_path": file_path,
                "bytes_written": len(content_bytes),
            }
        )
        return ToolResult(output=output)
    except Exception as exc:
        parent = "/".join(file_path.split("/")[:-1])
        return ToolResult(output=_path_error(exc, parent) or str(exc), is_error=True)


# ---------------------------------------------------------------------------
# List files
# ---------------------------------------------------------------------------


@tool(
    name="daytona_list_files",
    description="List files and directories in a given path.",
    read_only=True,
)
async def daytona_list_files(
    directory: str = ".",
    *,
    context: ToolExecutionContext,
) -> ToolResult:
    """List files in a directory in the Daytona sandbox.

    Args:
        directory: Directory path to list

    Returns:
        directory (str): Directory that was listed
        entries (list): File and directory names
    """
    sandbox = _get_sandbox(context)
    directory = _resolve_path(directory, context) if directory != "." else (_get_cwd(context) or ".")
    try:
        entries = await sandbox.fs.list_files(directory)
        names = []
        for entry in entries or []:
            name = getattr(entry, "name", None) or str(entry)
            names.append(name)
        output = json.dumps(
            {
                "cwd": _get_cwd(context) or "",
                "directory": directory,
                "entries": sorted(names),
            }
        )
        return ToolResult(output=output)
    except Exception as exc:
        return ToolResult(output=_path_error(exc, directory) or str(exc), is_error=True)


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
    sandbox = _get_sandbox(context)
    cwd = _get_cwd(context) or ""
    path = _resolve_path(path, context) if path != "." else (cwd or ".")
    try:
        matches = await sandbox.fs.find_files(path, pattern)
        if not matches:
            return ToolResult(
                output=json.dumps(
                    {
                        "cwd": cwd,
                        "pattern": pattern,
                        "path": path,
                        "matches": [],
                        "total_matches": 0,
                    }
                )
            )
        result_matches = []
        for match in matches[:500]:
            file_path = getattr(match, "file", None) or ""
            line_no = getattr(match, "line", None)
            content = getattr(match, "content", None) or ""
            result_matches.append(
                {
                    "file": file_path,
                    "line": line_no,
                    "content": content.rstrip(),
                }
            )
        return ToolResult(
            output=json.dumps(
                {
                    "cwd": cwd,
                    "pattern": pattern,
                    "path": path,
                    "matches": result_matches,
                    "total_matches": len(matches),
                }
            )
        )
    except Exception as exc:
        return ToolResult(output=_path_error(exc, path) or str(exc), is_error=True)


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
    sandbox = _get_sandbox(context)
    cwd = _get_cwd(context) or ""
    path = _resolve_path(path, context) if path != "." else (cwd or ".")
    try:
        # Use shell find for reliable glob — SDK search_files has issues
        # Strip leading **/ from glob patterns for find -name compatibility
        find_pattern = pattern.replace("**/", "")
        cmd = f"find {path} -name {find_pattern} -type f"
        resp = await sandbox.process.exec(cmd, timeout=30)
        file_list = [f for f in (resp.result or "").splitlines() if f.strip()][:500]
        return ToolResult(
            output=json.dumps(
                {
                    "cwd": cwd,
                    "pattern": pattern,
                    "path": path,
                    "files": file_list,
                    "total_files": len(file_list),
                }
            )
        )
    except Exception as exc:
        return ToolResult(output=_path_error(exc, path) or str(exc), is_error=True)
