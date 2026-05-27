"""Shared formatting for sandbox mutation tool results."""

from __future__ import annotations

import json
from typing import Any

from sandbox.shared.clock import normalize_timing_map
from tools._framework.core.results import ToolResult


def mutation_tool_result(
    *,
    success: bool,
    success_status: str,
    paths: list[str],
    failure_status: str | None = None,
    conflict_reason: str | None = None,
    error: dict[str, object] | None = None,
    mutation_source: str = "",
    changed_path_kinds: dict[str, str] | None = None,
    success_extra: dict[str, Any] | None = None,
    timings: dict[str, float] | None = None,
    metadata_extra: dict[str, Any] | None = None,
) -> ToolResult:
    """Return the common JSON shape for file mutation tools."""
    status = (
        success_status
        if success
        else failure_status or _failure_status(conflict_reason)
    )
    metadata: dict[str, Any] = {
        "status": status,
        "changed_paths": paths,
        "conflict_reason": conflict_reason,
    }
    if error:
        metadata["error_kind"] = str(error.get("kind") or "")
        metadata["error"] = dict(error)
    if mutation_source:
        metadata["mutation_source"] = mutation_source
    if changed_path_kinds:
        metadata["changed_path_kinds"] = dict(changed_path_kinds)
    if timings:
        metadata["timings"] = normalize_timing_map(timings)
    metadata.update(metadata_extra or {})

    if success:
        payload: dict[str, Any] = {
            "status": status,
            "changed_paths": paths,
            "conflict_reason": None,
        }
        if mutation_source:
            payload["mutation_source"] = mutation_source
        if changed_path_kinds:
            payload["changed_path_kinds"] = dict(changed_path_kinds)
        payload.update(success_extra or {})
        return ToolResult(output=json.dumps(payload), metadata=metadata)

    payload = {
        "status": status,
        "changed_paths": paths,
        "conflict_reason": conflict_reason or "",
    }
    if error:
        payload["error"] = dict(error)
    if mutation_source:
        payload["mutation_source"] = mutation_source
    if changed_path_kinds:
        payload["changed_path_kinds"] = dict(changed_path_kinds)
    return ToolResult(
        output=json.dumps(payload),
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
