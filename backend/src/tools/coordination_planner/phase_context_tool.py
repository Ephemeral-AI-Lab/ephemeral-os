"""Phase context tools — query outputs from completed planning phases."""

from __future__ import annotations

import json
import logging
from typing import Any

from tools.base import BaseTool, ToolExecutionContext, ToolResult
from tools.decorator import tool

logger = logging.getLogger(__name__)


def _serialize_for_json(obj: Any) -> str:
    try:
        return json.dumps(obj, default=str)
    except Exception:
        return str(obj)


# ---------------------------------------------------------------------------
# query_phase_context
# ---------------------------------------------------------------------------


def make_query_phase_context_tool(*, phase_outputs: dict[str, dict] | None = None) -> BaseTool:
    """Create a query_phase_context tool that captures phase_outputs via closure."""
    _phase_outputs = phase_outputs or {}

    @tool(
        name="query_phase_context",
        description=(
            "Query structured output from a completed planning phase. "
            "Use this to understand what earlier phases discovered."
        ),
    )
    async def query_phase_context(
        phase: str,
        key: str | None = None,
        *,
        context: ToolExecutionContext,
    ) -> ToolResult:
        """Query structured output from a completed planning phase.

        Args:
            phase: Phase name to query.
            key: Optional specific output key to retrieve. If omitted, returns the entire phase output.
        """
        phase_output = _phase_outputs.get(phase)
        if phase_output is None:
            available = list(_phase_outputs.keys())
            return ToolResult(
                output=json.dumps({
                    "error": f"Phase '{phase}' has no recorded output. Available phases: {available}",
                    "available_phases": available,
                }),
                is_error=True,
            )

        if key is not None:
            value = phase_output.get(key)
            if value is None:
                return ToolResult(
                    output=json.dumps({
                        "error": f"Key '{key}' not found in phase '{phase}' output.",
                        "available_keys": list(phase_output.keys()),
                    }),
                    is_error=True,
                )
            return ToolResult(output=_serialize_for_json({key: value}))

        return ToolResult(output=_serialize_for_json(phase_output))

    return query_phase_context


# ---------------------------------------------------------------------------
# list_phases
# ---------------------------------------------------------------------------


def make_list_phases_tool(*, phase_outputs: dict[str, dict] | None = None) -> BaseTool:
    """Create a list_phases tool that captures phase_outputs via closure."""
    _phase_outputs = phase_outputs or {}

    @tool(
        name="list_phases",
        description=(
            "List all completed planning phases and their output keys. "
            "Use this to discover which phases ran and what data they produced."
        ),
    )
    async def list_phases(*, context: ToolExecutionContext) -> ToolResult:
        """List all completed planning phases and their output keys."""
        if not _phase_outputs:
            return ToolResult(
                output=json.dumps({"phases": [], "note": "No phase outputs recorded yet."})
            )

        phases = []
        for name, output in _phase_outputs.items():
            phases.append({
                "phase": name,
                "keys": list(output.keys()),
            })

        return ToolResult(output=json.dumps({"phases": phases}))

    return list_phases
