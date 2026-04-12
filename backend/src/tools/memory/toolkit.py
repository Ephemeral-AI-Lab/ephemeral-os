"""Memory toolkit — cross-run exploration cache + edit history queries.

Merges the former exploration_memory and edit_history toolkits into one
toolkit for cross-run persistence and conflict prediction.

Tools:
- check_exploration_memory — check if a scope was recently explored
- save_exploration         — cache exploration findings for reuse
- query_edit_history       — query cross-run edit patterns for conflict prediction
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from pydantic import BaseModel, Field

from tools.core.base import BaseTool, BaseToolkit, ToolExecutionContext, ToolResult
from tools.exploration_memory.toolkit import get_exploration_memory


# ---------------------------------------------------------------------------
# CheckExplorationMemoryTool
# ---------------------------------------------------------------------------


class CheckExplorationMemoryInput(BaseModel):
    paths: list[str] = Field(..., description="Scope paths to check for cached exploration")


class CheckExplorationMemoryTool(BaseTool):
    name = "check_exploration_memory"
    description = (
        "Check if a scope was recently explored and files haven't changed. "
        "Returns 'cached' (with notes injected into Task Center) or 'needs_exploration'."
    )
    input_model = CheckExplorationMemoryInput

    async def execute(self, arguments: CheckExplorationMemoryInput, context: ToolExecutionContext) -> ToolResult:
        mem = get_exploration_memory()
        workspace_root = context.metadata.get("daytona_cwd", "") or context.metadata.get("ci_workspace_root", "")
        cached = await mem.check_async(arguments.paths, workspace_root)
        if cached is not None:
            tc = context.metadata.get("task_center")
            if tc:
                from team.models import Note
                for note_dict in cached:
                    tc.post(Note(**note_dict))
            return ToolResult(output=json.dumps({
                "status": "cached",
                "note_count": len(cached),
            }))
        return ToolResult(output=json.dumps({"status": "needs_exploration"}))


# ---------------------------------------------------------------------------
# SaveExplorationTool
# ---------------------------------------------------------------------------


class SaveExplorationInput(BaseModel):
    paths: list[str] = Field(..., description="Scope paths whose exploration to save")


class SaveExplorationTool(BaseTool):
    name = "save_exploration"
    description = "Save exploration findings to cache for cross-run reuse. Called automatically after explorer completes."
    input_model = SaveExplorationInput

    async def execute(self, arguments: SaveExplorationInput, context: ToolExecutionContext) -> ToolResult:
        mem = get_exploration_memory()
        workspace_root = context.metadata.get("daytona_cwd", "") or context.metadata.get("ci_workspace_root", "")
        tc = context.metadata.get("task_center")
        if tc is None:
            return ToolResult(output="No Task Center available.", is_error=True)
        notes = tc.read(scope_paths=arguments.paths)
        note_dicts = [asdict(n) for n in notes]
        await mem.save_async(arguments.paths, note_dicts, workspace_root)
        return ToolResult(output=json.dumps({
            "status": "saved",
            "note_count": len(note_dicts),
        }))


# ---------------------------------------------------------------------------
# QueryEditHistoryTool
# ---------------------------------------------------------------------------


class QueryEditHistoryInput(BaseModel):
    paths: list[str] = Field(..., description="Scope paths to query edit history for")
    limit: int = Field(default=10, description="Max hotspots to return")


class QueryEditHistoryTool(BaseTool):
    name = "query_edit_history"
    description = (
        "Query cross-run edit patterns to predict scope conflicts. "
        "Returns files edited by multiple agents across previous runs. Planner-only."
    )
    input_model = QueryEditHistoryInput

    async def execute(self, arguments: QueryEditHistoryInput, context: ToolExecutionContext) -> ToolResult:
        store = context.metadata.get("file_change_store")

        if store is not None and getattr(store, "initialized", False):
            hotspots = store.contention_hotspots(
                scope_prefixes=arguments.paths,
                limit=arguments.limit,
            )
            if not hotspots:
                return ToolResult(output=json.dumps({
                    "hotspots": [],
                    "note": "No contention history found for these paths.",
                }))
            return ToolResult(output=json.dumps({
                "hotspots": [
                    {
                        "file": h.file_path,
                        "agents_touched": h.agent_count,
                        "total_edits": h.edit_count,
                    }
                    for h in hotspots
                ],
            }))

        # Fallback: check in-memory Arbiter for same-run history
        arbiter = context.metadata.get("arbiter")
        if arbiter is not None:
            hotspots_map: dict[str, set[str]] = {}
            edit_counts: dict[str, int] = {}
            for entry in arbiter.changes_since(0):
                if any(entry.file_path.startswith(p.rstrip("/")) for p in arguments.paths):
                    hotspots_map.setdefault(entry.file_path, set()).add(entry.agent_id)
                    edit_counts[entry.file_path] = edit_counts.get(entry.file_path, 0) + 1
            multi_agent = [
                {"file": fp, "agents_touched": len(agents), "total_edits": edit_counts[fp]}
                for fp, agents in hotspots_map.items()
                if len(agents) > 1
            ]
            multi_agent.sort(key=lambda x: (-x["agents_touched"], -x["total_edits"]))
            return ToolResult(output=json.dumps({
                "hotspots": multi_agent[:arguments.limit],
                "note": "In-memory only (same-run history). Connect PostgreSQL for cross-run data.",
            }))

        return ToolResult(output=json.dumps({
            "hotspots": [],
            "note": "No edit history available (no Arbiter or FileChangeStore).",
        }))


# ---------------------------------------------------------------------------
# Toolkit
# ---------------------------------------------------------------------------


class MemoryToolkit(BaseToolkit):
    def __init__(self) -> None:
        super().__init__(
            name="memory",
            description="Cross-run memory: exploration cache and edit history for conflict prediction.",
            tools=[
                CheckExplorationMemoryTool(),
                SaveExplorationTool(),
                QueryEditHistoryTool(),
            ],
        )

    @classmethod
    def from_context(cls, ctx: Any) -> MemoryToolkit:
        return cls()
