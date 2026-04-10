"""File editing tool for Daytona sandboxes."""

from __future__ import annotations

import hashlib
import json
import logging

from tools.core.base import ToolExecutionContext, ToolResult
from tools.daytona_toolkit.tools import _get_cwd, _path_error, _resolve_path
from tools.daytona_toolkit.ci_integration import (
    abort_ci_write,
    finalize_ci_write,
    get_ci_service,
    prepare_ci_write,
)
from tools.core.decorator import tool

logger = logging.getLogger(__name__)

_OUTPUT_MAX_CHARS = 8000


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


@tool(
    name="daytona_edit_file",
    description="Edit a file using search-and-replace on the first match.",
)
async def daytona_edit_file(
    file_path: str,
    old_text: str,
    new_text: str,
    description: str = "",
    dry_run: bool = False,
    *,
    context: ToolExecutionContext,
) -> ToolResult:
    """Edit a file in the Daytona sandbox via search-and-replace.

    Args:
        file_path: Path to the file to edit
        old_text: Text to find and replace (first occurrence)
        new_text: Replacement text
        description: Optional description of the edit
        dry_run: Preview the edit without applying

    Returns:
        file_path (str): Path to the edited file
        status (str): Edit result — edited, dry_run, or error
        diff (str): Unified diff preview (dry_run only)
    """
    sandbox = context.metadata.get("daytona_sandbox")
    if sandbox is None:
        return ToolResult(
            output="No Daytona sandbox in context.",
            is_error=True,
        )

    file_path = _resolve_path(file_path, context)

    prepared = None
    current = ""
    current_hash = ""
    svc = get_ci_service(context)
    if svc is not None and hasattr(svc, "prepare_write"):
        prepared, scope_packet, err = prepare_ci_write(context, file_path)
        if err is not None:
            return ToolResult(
                output=err,
                is_error=True,
                metadata={"scope_packet": scope_packet, "conflict": True},
            )
        if prepared is None:
            return ToolResult(
                output=f"CI service unavailable for coordinated edit of {file_path}",
                is_error=True,
            )
        if not bool(getattr(prepared, "existed", True)):
            abort_ci_write(context, prepared)
            return ToolResult(
                output=f"Path does not exist: {file_path}",
                is_error=True,
            )
        current = str(getattr(prepared, "current_content", "") or "")
        current_hash = str(getattr(prepared, "current_hash", "") or "")
    else:
        try:
            raw = await sandbox.fs.download_file(file_path)
            current = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
            current_hash = _content_hash(current)
        except Exception as exc:
            return ToolResult(output=_path_error(exc, file_path) or f"Cannot read file: {exc}", is_error=True)

    # Check that old_text exists
    if old_text not in current:
        abort_ci_write(context, prepared)
        return ToolResult(
            output=f"Search text not found in {file_path}",
            is_error=True,
        )

    # Apply edit
    new_content = current.replace(old_text, new_text, 1)

    if dry_run:
        # Show preview
        import difflib

        diff = difflib.unified_diff(
            current.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=f"a/{file_path}",
            tofile=f"b/{file_path}",
            lineterm="",
        )
        diff_text = "".join(diff)
        if len(diff_text) > _OUTPUT_MAX_CHARS:
            diff_text = diff_text[:_OUTPUT_MAX_CHARS] + "\n... (truncated)"
        output = json.dumps(
            {
                "cwd": _get_cwd(context) or "",
                "file_path": file_path,
                "status": "dry_run",
                "occ": False,
                "diff": diff_text,
            }
        )
        abort_ci_write(context, prepared)
        return ToolResult(output=output, metadata={"dry_run": True})

    # Try OCC-coordinated edit via CI service
    if prepared is not None:
        try:
            result = finalize_ci_write(
                context,
                prepared,
                content=new_content,
                edit_type="edit",
                description=description,
            )
        finally:
            abort_ci_write(context, prepared)
        if getattr(result, "success", False):
            output = json.dumps(
                {
                    "cwd": _get_cwd(context) or "",
                    "file_path": file_path,
                    "status": "edited",
                    "occ": True,
                    "expected_hash": current_hash,
                }
            )
            return ToolResult(
                output=output,
                metadata={"file_path": file_path, "occ": True},
            )
        return ToolResult(
            output=str(getattr(result, "message", "") or "Edit failed"),
            is_error=True,
            metadata={"conflict": bool(getattr(result, "conflict", False))},
        )
    else:
        # Direct write (no CI)
        try:
            await sandbox.fs.upload_file(new_content.encode("utf-8"), file_path)
            output = json.dumps(
                {
                    "cwd": _get_cwd(context) or "",
                    "file_path": file_path,
                    "status": "edited",
                    "occ": False,
                }
            )
            return ToolResult(
                output=output,
                metadata={"file_path": file_path, "occ": False},
            )
        except Exception as exc:
            return ToolResult(output=_path_error(exc, file_path) or f"Write failed: {exc}", is_error=True)
