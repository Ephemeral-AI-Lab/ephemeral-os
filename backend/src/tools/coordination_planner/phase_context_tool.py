"""Phase context tools — query outputs from completed planning phases."""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, Field

from tools.base import BaseTool, ToolExecutionContext, ToolResult

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

    phase: str = Field(description="Phase name to query.")
    key: str | None = Field(
        default=None,
        description="Optional specific output key to retrieve. If omitted, returns the entire phase output.",
    )


class QueryPhaseContextTool(BaseTool):
    """Query structured output from a completed planning phase."""

    name = "query_phase_context"
    description = (
        "Query structured output from a completed planning phase. "
        "Use this to understand what earlier phases discovered."
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
        "Use this to discover which phases ran and what data they produced."
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
            phases.append({
                "phase": name,
                "keys": list(output.keys()),
            })

        return ToolResult(output=json.dumps({"phases": phases}))
