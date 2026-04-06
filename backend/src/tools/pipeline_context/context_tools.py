"""Pipeline context tools — query outputs from completed pipeline steps."""

from __future__ import annotations

import json
from typing import Any

from tools.core.base import BaseTool, ToolExecutionContext, ToolResult
from tools.core.decorator import tool


def _serialize(obj: Any) -> str:
    try:
        return json.dumps(obj, default=str)
    except Exception:
        return str(obj)


# ---------------------------------------------------------------------------
# query_pipeline_context
# ---------------------------------------------------------------------------


def make_query_pipeline_context_tool(*, context_map: dict[str, dict] | None = None) -> BaseTool:
    """Create a query_pipeline_context tool that captures context_map via closure."""
    _context_map = context_map or {}

    @tool(
        name="query_pipeline_context",
        description=(
            "Query structured output from a completed pipeline step. "
            "Use this to read data produced by earlier steps."
        ),
        read_only=True,
    )
    async def query_pipeline_context(
        step: str,
        key: str | None = None,
        *,
        context: ToolExecutionContext,
    ) -> ToolResult:
        """Query structured output from a completed pipeline step.

        Args:
            step: Step name to query.
            key: Optional specific output key. If omitted, returns the entire step output.
        """
        step_output = _context_map.get(step)
        if step_output is None:
            available = list(_context_map.keys())
            return ToolResult(
                output=json.dumps({
                    "error": f"Step '{step}' has no recorded output.",
                    "available_steps": available,
                }),
                is_error=True,
            )

        if key is not None:
            value = step_output.get(key)
            if value is None:
                return ToolResult(
                    output=json.dumps({
                        "error": f"Key '{key}' not found in step '{step}' output.",
                        "available_keys": list(step_output.keys()),
                    }),
                    is_error=True,
                )
            return ToolResult(output=_serialize({key: value}))

        return ToolResult(output=_serialize(step_output))

    return query_pipeline_context


# ---------------------------------------------------------------------------
# list_pipeline_steps
# ---------------------------------------------------------------------------


def make_list_pipeline_steps_tool(*, context_map: dict[str, dict] | None = None) -> BaseTool:
    """Create a list_pipeline_steps tool that captures context_map via closure."""
    _context_map = context_map or {}

    @tool(
        name="list_pipeline_steps",
        description=(
            "List all completed pipeline steps and their output keys. "
            "Use this to discover what data is available from prior steps."
        ),
        read_only=True,
    )
    async def list_pipeline_steps(*, context: ToolExecutionContext) -> ToolResult:
        """List all completed pipeline steps and their output keys."""
        if not _context_map:
            return ToolResult(
                output=json.dumps({"steps": [], "note": "No step outputs recorded yet."})
            )
        steps = [
            {"step": name, "keys": list(output.keys())}
            for name, output in _context_map.items()
        ]
        return ToolResult(output=json.dumps({"steps": steps}))

    return list_pipeline_steps


# ---------------------------------------------------------------------------
# get_pipeline_metadata
# ---------------------------------------------------------------------------


def make_get_pipeline_metadata_tool(
    *,
    pipeline_meta: dict | None = None,
    current_step: str | None = None,
) -> BaseTool:
    """Create a get_pipeline_metadata tool that captures pipeline_meta and current_step via closure."""
    _pipeline_meta = pipeline_meta or {}
    _current_step = current_step

    @tool(
        name="get_pipeline_metadata",
        description=(
            "Get pipeline-level metadata including the goal, current step, "
            "and pipeline configuration."
        ),
        read_only=True,
    )
    async def get_pipeline_metadata(*, context: ToolExecutionContext) -> ToolResult:
        """Get pipeline-level metadata (goal, config, current step)."""
        meta = dict(_pipeline_meta)
        meta["current_step"] = _current_step
        return ToolResult(output=_serialize(meta))

    return get_pipeline_metadata
