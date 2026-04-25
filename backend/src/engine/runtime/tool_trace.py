"""Per-turn tool trace bookkeeping used by the query loop."""

from __future__ import annotations

from tools.core.runtime import ExecutionMetadata

_TOOL_TRACE_LIMIT = 64


def _normalize_trace_paths(value: object) -> list[str]:
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            if isinstance(item, str):
                stripped = item.strip()
                if stripped:
                    out.append(stripped)
        return out
    return []


def _append_trace_values(
    metadata: ExecutionMetadata | None,
    key: str,
    values: list[str],
) -> None:
    if metadata is None or not values:
        return
    existing = _normalize_trace_paths(metadata.get(key, []))
    seen = set(existing)
    for value in values:
        if value not in seen:
            existing.append(value)
            seen.add(value)
    if len(existing) > _TOOL_TRACE_LIMIT:
        existing = existing[-_TOOL_TRACE_LIMIT:]
    metadata[key] = existing


def _increment_trace_counter(metadata: ExecutionMetadata | None, key: str) -> None:
    if metadata is None:
        return
    current = metadata.get(key, 0)
    metadata[key] = int(current) + 1 if isinstance(current, (int, float)) else 1


def record_tool_trace(
    metadata: ExecutionMetadata | None,
    tool_name: str,
    tool_input: dict[str, object],
    *,
    tool_use_id: str | None = None,
) -> None:
    if metadata is None:
        return
    if tool_name == "read_task_details":
        _increment_trace_counter(metadata, "_read_task_details_calls")
        return
    if tool_name == "read_file_note":
        _increment_trace_counter(metadata, "_read_file_note_calls")
        _append_trace_values(
            metadata,
            "_note_read_paths_this_turn",
            _normalize_trace_paths(tool_input.get("file_paths")),
        )
        return
    if tool_name == "ci_query_symbol":
        _increment_trace_counter(metadata, "_ci_context_calls")
        _increment_trace_counter(metadata, "_ci_query_symbol_calls")
        return
    if tool_name == "ci_workspace_structure":
        _increment_trace_counter(metadata, "_ci_context_calls")
        _increment_trace_counter(metadata, "_ci_workspace_structure_calls")
        return
    if tool_name == "ci_diagnostics":
        _increment_trace_counter(metadata, "_ci_context_calls")
        _increment_trace_counter(metadata, "_ci_diagnostics_calls")
        return
    if tool_name == "daytona_shell":
        _increment_trace_counter(metadata, "_daytona_shell_calls")
        return
    if tool_name == "daytona_read_file":
        _increment_trace_counter(metadata, "_daytona_read_file_calls")
        _append_trace_values(
            metadata,
            "_read_paths_this_turn",
            _normalize_trace_paths(tool_input.get("file_path")),
        )
        return
    if tool_name != "run_subagent" or tool_input.get("agent_name") != "scout":
        return
    current_launches = metadata.get("_scout_launches_this_turn", 0)
    if tool_use_id:
        seen_ids = _normalize_trace_paths(metadata.get("_scout_trace_tool_use_ids_this_turn", []))
        if tool_use_id in seen_ids:
            return
        launch_order = int(current_launches) + 1 if isinstance(current_launches, (int, float)) else 1
        seen_ids.append(tool_use_id)
        if len(seen_ids) > _TOOL_TRACE_LIMIT:
            seen_ids = seen_ids[-_TOOL_TRACE_LIMIT:]
        metadata["_scout_trace_tool_use_ids_this_turn"] = seen_ids
        launch_order_raw = metadata.get("_scout_launch_order_by_tool_use_id", {})
        launch_order_map = launch_order_raw.copy() if isinstance(launch_order_raw, dict) else {}
        launch_order_map[tool_use_id] = launch_order
        metadata["_scout_launch_order_by_tool_use_id"] = launch_order_map
    metadata["_scout_launches_this_turn"] = int(current_launches) + 1 if isinstance(current_launches, (int, float)) else 1
