"""Provider-history compaction for background task snapshots."""

from __future__ import annotations

import copy
from typing import Any

from message import (
    ContentBlock,
    ConversationMessage,
    ToolResultBlock,
    ToolUseBlock,
)
from tools import (
    build_background_snapshot_metadata,
    render_background_snapshot,
)

_BACKGROUND_SNAPSHOT_TOOLS: frozenset[str] = frozenset({"wait_background_tasks"})
_REDUCIBLE_RUNNING_STATUSES: frozenset[str] = frozenset({"running"})
_REDUCIBLE_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"completed", "failed", "cancelled", "delivered"}
)
_REDUCIBLE_STATUSES: frozenset[str] = (
    _REDUCIBLE_RUNNING_STATUSES | _REDUCIBLE_TERMINAL_STATUSES
)


def reduce_background_task_history(
    messages: list[ConversationMessage],
) -> list[ConversationMessage]:
    """Keep only the latest provider-visible state for each background task."""
    tool_use_map: dict[str, tuple[int, int, str]] = {}
    snapshot_tool_use_ids: set[str] = set()
    _WinnerKey = tuple[bool, int, int, int, str, int]
    winners: dict[str, _WinnerKey] = {}

    for msg_idx, msg in enumerate(messages):
        if msg.role != "assistant":
            continue
        for block_idx, block in enumerate(msg.content):
            if isinstance(block, ToolUseBlock):
                tool_use_map[block.id] = (msg_idx, block_idx, block.name)

    for msg_idx, msg in enumerate(messages):
        for block_idx, block in enumerate(msg.content):
            if not isinstance(block, ToolResultBlock):
                continue
            snapshot = _background_snapshot_info(block, tool_use_map)
            if snapshot is None:
                continue
            snapshot_tool_use_ids.add(block.tool_use_id)
            for status_idx, entry in enumerate(snapshot["statuses"]):
                task_id = entry.get("task_id")
                status = entry.get("status")
                if not isinstance(task_id, str) or status not in _REDUCIBLE_STATUSES:
                    continue
                is_terminal = status in _REDUCIBLE_TERMINAL_STATUSES
                key = (
                    is_terminal,
                    msg_idx,
                    block_idx,
                    status_idx,
                    block.tool_use_id,
                    status_idx,
                )
                current = winners.get(task_id)
                if current is None or key[:4] > current[:4]:
                    winners[task_id] = key

    keep_snapshot_statuses: dict[str, set[int]] = {}
    for winner in winners.values():
        keep_snapshot_statuses.setdefault(winner[4], set()).add(winner[5])

    drop_tool_use_ids = snapshot_tool_use_ids - keep_snapshot_statuses.keys()

    reduced: list[ConversationMessage] = []
    for msg in messages:
        new_content: list[ContentBlock] = []
        for block in msg.content:
            if isinstance(block, ToolUseBlock) and block.id in drop_tool_use_ids:
                continue

            if isinstance(block, ToolResultBlock):
                snapshot = _background_snapshot_info(block, tool_use_map)
                if snapshot is None:
                    new_content.append(block.model_copy(deep=True))
                    continue
                keep_indexes = keep_snapshot_statuses.get(block.tool_use_id)
                if not keep_indexes:
                    continue
                filtered = [
                    copy.deepcopy(status)
                    for idx, status in enumerate(snapshot["statuses"])
                    if idx in keep_indexes
                ]
                rebuilt = block.model_copy(deep=True)
                rebuilt.content = render_background_snapshot(
                    snapshot["kind"],
                    filtered,
                    elapsed_seconds=snapshot["elapsed_seconds"],
                )
                rebuilt.metadata = build_background_snapshot_metadata(
                    snapshot["kind"],
                    snapshot["scope"],
                    filtered,
                    elapsed_seconds=snapshot["elapsed_seconds"],
                )
                new_content.append(rebuilt)
                continue

            new_content.append(block.model_copy(deep=True))

        if new_content:
            reduced.append(ConversationMessage(role=msg.role, content=new_content))
    return reduced


def _background_snapshot_info(
    block: ToolResultBlock,
    tool_use_map: dict[str, tuple[int, int, str]],
) -> dict[str, Any] | None:
    if not block.metadata:
        return None
    snapshot = block.metadata.get("background_snapshot")
    if not isinstance(snapshot, dict):
        return None
    tool_use = tool_use_map.get(block.tool_use_id)
    if tool_use is None or tool_use[2] not in _BACKGROUND_SNAPSHOT_TOOLS:
        return None
    statuses = snapshot.get("statuses")
    kind = snapshot.get("kind")
    scope = snapshot.get("scope")
    if not isinstance(statuses, list) or not isinstance(kind, str) or not isinstance(scope, str):
        return None
    elapsed = snapshot.get("elapsed_seconds")
    if not isinstance(elapsed, (int, float)):
        elapsed = None
    return {
        "kind": kind,
        "scope": scope,
        "statuses": statuses,
        "elapsed_seconds": elapsed,
    }


__all__ = ["reduce_background_task_history"]
