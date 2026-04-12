"""Search toolkit — keyword search across Task Center and Ledger-based scope change queries."""

from __future__ import annotations

import json
import time
from typing import Any

from pydantic import BaseModel, Field

from tools.core.base import BaseTool, BaseToolkit, ToolExecutionContext, ToolResult


class SearchContextInput(BaseModel):
    query: str = Field(..., description="Search query (keyword match)")
    scope_paths: list[str] | None = Field(default=None, description="Limit search to these scopes")
    limit: int = Field(default=20, description="Max results")


class SearchContextTool(BaseTool):
    name = "search_context"
    description = "Search Task Center notes by keyword."
    input_model = SearchContextInput

    async def execute(self, arguments: SearchContextInput, context: ToolExecutionContext) -> ToolResult:
        tc = context.metadata.get("task_center")
        if tc is None:
            return ToolResult(output="No Task Center available.", is_error=True)
        notes = tc.read(scope_paths=arguments.scope_paths, limit=arguments.limit)
        query_lower = arguments.query.lower()
        matches = [n for n in notes if query_lower in n.content.lower()]
        if not matches:
            return ToolResult(output="No matching notes found.")
        lines: list[str] = []
        for n in matches[: arguments.limit]:
            lines.append(f"### {n.agent_name} ({n.task_id})")
            lines.append(n.content[:500])
            lines.append("")
        return ToolResult(output="\n".join(lines))


class ScopeChangedSinceInput(BaseModel):
    paths: list[str] = Field(..., description="Scope paths to check")
    since: float | None = Field(default=None, description="Unix timestamp. Defaults to task start time.")


class ScopeChangedSinceTool(BaseTool):
    name = "scope_changed_since"
    description = "Check what files changed in your scope since a given time. Uses Ledger (ground truth) with Task Center note fallback."
    input_model = ScopeChangedSinceInput

    async def execute(self, arguments: ScopeChangedSinceInput, context: ToolExecutionContext) -> ToolResult:
        since = arguments.since or context.metadata.get("work_item_started_at", 0)
        ledger = context.metadata.get("ledger")

        if ledger is not None:
            # Ground truth: query actual file changes from Ledger
            changes = ledger.changes_since(since)
            scoped = [
                e for e in changes
                if any(e.file_path.startswith(p.rstrip("/")) for p in arguments.paths)
            ]
            if not scoped:
                return ToolResult(output="No changes detected in scope since the given time.")
            now = time.time()
            lines = [f"Changes in scope since {since}:"]
            for e in scoped:
                lines.append(
                    f"- {e.file_path} ({e.edit_type} by {e.agent_id}, "
                    f"{int(now - e.timestamp)}s ago)"
                )
            return ToolResult(output="\n".join(lines))

        # Fallback: query Task Center notes
        tc = context.metadata.get("task_center")
        if tc is None:
            return ToolResult(output="No changes detected.")
        notes = tc.read(scope_paths=arguments.paths, since=since)
        if not notes:
            return ToolResult(output="No changes detected in scope since the given time.")
        lines = [f"Changes in scope since {since}:"]
        for n in notes:
            lines.append(f"- {n.agent_name}: {n.content[:200]}")
        return ToolResult(output="\n".join(lines))


class ContextChangedSinceInput(BaseModel):
    pass  # No arguments needed — uses task start time


class ContextChangedSinceTool(BaseTool):
    name = "context_changed_since"
    description = "Check if your context has changed since task started. Call before committing multi-file changes."
    input_model = ContextChangedSinceInput

    async def execute(self, arguments: ContextChangedSinceInput, context: ToolExecutionContext) -> ToolResult:
        since = context.metadata.get("work_item_started_at", 0)
        task_id = context.metadata.get("work_item_id", "")
        agent_run_id = context.metadata.get("agent_run_id", "")

        scope_changes = 0
        new_notes_by_others = 0

        # Check Ledger for file changes in scope by other agents
        ledger = context.metadata.get("ledger")
        scope_paths = context.metadata.get("write_scope") or []
        if ledger is not None and scope_paths:
            changes = ledger.changes_since(since)
            scope_changes = sum(
                1 for e in changes
                if e.agent_id != agent_run_id
                and any(e.file_path.startswith(p.rstrip("/")) for p in scope_paths)
            )

        # Check Task Center for new notes by others
        tc = context.metadata.get("task_center")
        if tc is not None:
            all_recent = tc.read(since=since)
            new_notes_by_others = sum(1 for n in all_recent if n.task_id != task_id)

        stale = scope_changes > 0 or new_notes_by_others > 0
        return ToolResult(output=json.dumps({
            "stale": stale,
            "scope_changes_by_others": scope_changes,
            "new_notes_by_others": new_notes_by_others,
            "suggestion": "Re-read affected files and check Task Center "
                          "for new context before committing." if stale else None,
        }))


class SearchToolkit(BaseToolkit):
    def __init__(self) -> None:
        super().__init__(
            name="search",
            description="Search Task Center notes by keyword and check scope changes.",
            tools=[
                SearchContextTool(),
                ScopeChangedSinceTool(),
                ContextChangedSinceTool(),
            ],
        )

    @classmethod
    def from_context(cls, ctx: Any) -> SearchToolkit:
        return cls()
