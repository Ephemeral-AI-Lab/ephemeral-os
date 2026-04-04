"""Phase context tools — query outputs from completed planning phases."""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, Field

from ephemeralos.tools.base import BaseTool, ToolExecutionContext, ToolResult

logger = logging.getLogger(__name__)


def _serialize_for_json(obj: Any) -> str:
    try:
        return json.dumps(obj, default=str)
    except Exception:
        return str(obj)


# ---------------------------------------------------------------------------
# query_phase_context
# ---------------------------------------------------------------------------


class QueryPhaseContextInput(BaseModel):
    """Arguments for querying a planning phase's output."""

    phase: str = Field(description="Phase name to query (e.g. 'analyze', 'explore', 'synthesize')")
    key: str | None = Field(
        default=None,
        description="Optional specific output key to retrieve. If omitted, returns the entire phase output.",
    )


class QueryPhaseContextTool(BaseTool):
    """Query structured output from a completed planning phase."""

    name = "query_phase_context"
    description = (
        "Query structured output from a completed planning phase. "
        "Use this to understand what earlier phases discovered before decomposing tasks."
    )
    input_model = QueryPhaseContextInput

    def __init__(self, *, phase_outputs: dict[str, dict] | None = None) -> None:
        self._phase_outputs = phase_outputs or {}

    async def execute(
        self, arguments: QueryPhaseContextInput, context: ToolExecutionContext
    ) -> ToolResult:
        phase_output = self._phase_outputs.get(arguments.phase)
        if phase_output is None:
            available = list(self._phase_outputs.keys())
            return ToolResult(
                output=json.dumps({
                    "error": f"Phase '{arguments.phase}' has no recorded output. Available phases: {available}",
                    "available_phases": available,
                }),
                is_error=True,
            )

        if arguments.key is not None:
            value = phase_output.get(arguments.key)
            if value is None:
                return ToolResult(
                    output=json.dumps({
                        "error": f"Key '{arguments.key}' not found in phase '{arguments.phase}' output.",
                        "available_keys": list(phase_output.keys()),
                    }),
                    is_error=True,
                )
            return ToolResult(output=_serialize_for_json({arguments.key: value}))

        return ToolResult(output=_serialize_for_json(phase_output))


# ---------------------------------------------------------------------------
# list_phases
# ---------------------------------------------------------------------------


class _EmptyInput(BaseModel):
    """No parameters required."""


class ListPhasesTool(BaseTool):
    """List all completed planning phases and their output keys."""

    name = "list_phases"
    description = (
        "List all completed planning phases and their output keys. "
        "Use this first to discover which phases ran and what data they produced."
    )
    input_model = _EmptyInput

    def __init__(self, *, phase_outputs: dict[str, dict] | None = None) -> None:
        self._phase_outputs = phase_outputs or {}

    async def execute(self, arguments: _EmptyInput, context: ToolExecutionContext) -> ToolResult:
        if not self._phase_outputs:
            return ToolResult(
                output=json.dumps({"phases": [], "note": "No phase outputs recorded yet."})
            )

        phases = []
        for name, output in self._phase_outputs.items():
            summary_parts = []
            for k in (
                "summary",
                "region_count",
                "success_count",
                "partial_success_count",
                "failed_count",
                "report_count",
                "task_count",
            ):
                if k in output:
                    summary_parts.append(f"{k}={output[k]}")
            summary = "; ".join(summary_parts) if summary_parts else str(output)[:120]
            phases.append({"phase": name, "keys": list(output.keys()), "summary": summary})

        return ToolResult(output=json.dumps({"phases": phases}))


# ---------------------------------------------------------------------------
# query_exploration_context
# ---------------------------------------------------------------------------


class QueryExplorationContextInput(BaseModel):
    """Arguments for querying exploration context about a file."""

    file_path: str = Field(description="File path to query (relative to repo root)")


class QueryExplorationContextTool(BaseTool):
    """Query what is known about a file from exploration and sibling workers."""

    name = "query_exploration_context"
    description = (
        "Query what is known about a file path from exploration and sibling workers. "
        "Returns exploration depth, discovered symbols, which tasks claimed or modified "
        "the file, and whether siblings changed it. Use before editing shared files."
    )
    input_model = QueryExplorationContextInput

    async def execute(
        self, arguments: QueryExplorationContextInput, context: ToolExecutionContext
    ) -> ToolResult:
        normalized = arguments.file_path.strip().lstrip("/")
        if not normalized:
            return ToolResult(output=json.dumps({"error": "empty file path"}), is_error=True)

        # Exploration ledger is injected via context metadata
        exploration_ledger = context.metadata.get("exploration_ledger")
        if exploration_ledger is None:
            return ToolResult(
                output=json.dumps({
                    "path": normalized,
                    "note": "No exploration ledger available in this context.",
                })
            )

        with exploration_ledger._lock:
            entry = exploration_ledger._files.get(normalized)

        if entry is None:
            covered = exploration_ledger.has_exploration_covering(normalized)
            return ToolResult(
                output=json.dumps({
                    "path": normalized,
                    "in_ledger": False,
                    "exploration_covers_scope": covered,
                    "note": (
                        "Parent scope was explored."
                        if covered
                        else "No exploration covers this path."
                    ),
                })
            )

        depth_labels = {0: "stat", 1: "listed", 2: "read", 3: "symbol-parsed"}
        result = {
            "path": normalized,
            "in_ledger": True,
            "exists": entry.exists,
            "exploration_depth": entry.exploration_depth,
            "exploration_depth_label": depth_labels.get(entry.exploration_depth, "unknown"),
            "explored_by": entry.explored_by,
            "symbols": entry.symbols_exported[:15] if entry.symbols_exported else [],
            "claimed_by": entry.claimed_by,
            "modified_by": entry.modified_by,
            "shared": len(entry.claimed_by) > 1,
        }
        return ToolResult(output=json.dumps(result))
