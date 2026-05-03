"""Shared formatting for sandbox mutation tool results."""

from __future__ import annotations

import json
from typing import Any

from tools.core.results import ToolResult


def mutation_tool_result(
    *,
    tool_name: str,
    success: bool,
    success_status: str,
    paths: list[str],
    warnings: list[str] | None = None,
    conflict_reason: str | None = None,
    success_extra: dict[str, Any] | None = None,
    metadata_extra: dict[str, Any] | None = None,
) -> ToolResult:
    """Return the common JSON shape for file mutation tools."""
    warnings_list = list(warnings or [])
    metadata: dict[str, Any] = {
        "tool": tool_name,
        "file_count": len(paths),
        "success_count": len(paths) if success else 0,
        "status": success_status if success else _failure_status(conflict_reason),
        "changed_paths": paths,
        "conflict_reason": conflict_reason,
    }
    metadata.update(metadata_extra or {})

    if success:
        payload: dict[str, Any] = {
            "tool": tool_name,
            "status": success_status,
            "paths": paths,
            "warnings": warnings_list,
        }
        payload.update(success_extra or {})
        return ToolResult(output=json.dumps(payload), metadata=metadata)

    status = _failure_status(conflict_reason)
    return ToolResult(
        output=json.dumps(
            {
                "tool": tool_name,
                "status": status,
                "paths": paths,
                "warnings": warnings_list,
                "conflict_file": paths[0] if paths else "",
                "conflict_reason": conflict_reason or "",
                "message": conflict_reason or "operation failed",
            }
        ),
        is_error=True,
        metadata=metadata,
    )


def _failure_status(conflict_reason: str | None) -> str:
    if conflict_reason in {"base_mismatch", "version_conflict", "drift"}:
        return "aborted_version"
    if conflict_reason in {"lock_conflict", "locked"}:
        return "aborted_lock"
    if conflict_reason in {"not_found", "missing"}:
        return "not_found"
    return "failed"


__all__ = ["mutation_tool_result"]
