"""File-oriented CI tool — bounded file reads via CI cache."""

from __future__ import annotations

import json
import logging

from tools.core.base import ToolExecutionContext, ToolResult
from tools.daytona_toolkit.ci_integration import (
    get_ci_service,
    get_daytona_sandbox,
    resolve_daytona_path,
)
from tools.core.decorator import tool

logger = logging.getLogger(__name__)

_MAX_LINES = 500
_MAX_CHARS = 32_000


@tool(name="ci_read_file", description="Read file contents from the workspace sandbox with line numbers.", read_only=True)
async def ci_read_file(
    path: str,
    start_line: int = 1,
    max_lines: int = 200,
    *,
    context: ToolExecutionContext,
) -> ToolResult:
    """Read a file from the workspace via CI cache.

    Args:
        path: File path to read
        start_line: First line to read (1-based)
        max_lines: Maximum lines to return

    Returns:
        file_path (str): The file path
        start_line (int): First line returned
        end_line (int): Last line returned
        total_lines (int): Total lines in file
        truncated (bool): Whether file was truncated
        content (str): File contents with line numbers
    """
    svc = get_ci_service(context)

    # Try reading from tree cache first
    content = None
    if svc:
        entry = svc.tree_cache.get_tree(path)
        if entry:
            content = entry.content

    # Fall back to direct file read
    if content is None:
        sandbox = get_daytona_sandbox(context)
        if sandbox is not None:
            remote_path = resolve_daytona_path(path, context)
            try:
                raw = await sandbox.fs.download_file(remote_path)
                content = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
                path = remote_path
            except UnicodeDecodeError:
                return ToolResult(output=f"Binary file: {remote_path}", is_error=True)
            except Exception:
                logger.debug("Remote ci_read_file failed for %s", remote_path, exc_info=True)

    if content is None:
        try:
            from pathlib import Path
            p = Path(path)
            if not p.is_file():
                return ToolResult(output=f"File not found: {path}", is_error=True)
            content = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return ToolResult(output=f"Binary file: {path}", is_error=True)
        except Exception as exc:
            return ToolResult(output=str(exc), is_error=True)

    # Truncate large files
    if len(content) > _MAX_CHARS:
        content = content[:_MAX_CHARS]
        truncated = True
    else:
        truncated = False

    lines = content.splitlines()
    total = len(lines)
    start = max(1, start_line)
    end = min(total, start + max_lines - 1)

    selected = []
    for i in range(start, end + 1):
        selected.append(f"{i:4d}: {lines[i - 1]}")

    result = {
        "file_path": path,
        "start_line": start,
        "end_line": end,
        "total_lines": total,
        "truncated": truncated,
        "content": "\n".join(selected),
    }

    return ToolResult(output=json.dumps(result, indent=2))
