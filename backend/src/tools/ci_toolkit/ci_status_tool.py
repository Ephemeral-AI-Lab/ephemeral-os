"""Code-intelligence status tool."""

from __future__ import annotations

from tools.ci_toolkit.query_tools import (
    CiStatusInput,
    CiStatusOutput,
    run_ci_status,
)
from tools.core.base import ToolExecutionContext, ToolResult
from tools.core.decorator import tool


@tool(
    name="ci_status",
    description="Check code intelligence readiness: cache, index, LSP, and optional edit hotspot activity.",
    short_description="Check code intelligence status.",
    input_model=CiStatusInput,
    output_model=CiStatusOutput,
)
async def ci_status(
    include_edit_hotspots: bool = True,
    hotspot_limit: int = 10,
    hotspot_cross_run: bool = False,
    *,
    context: ToolExecutionContext,
) -> ToolResult:
    """Check code intelligence service readiness."""
    return await run_ci_status(
        include_edit_hotspots=include_edit_hotspots,
        hotspot_limit=hotspot_limit,
        hotspot_cross_run=hotspot_cross_run,
        context=context,
    )


__all__ = ["ci_status"]
