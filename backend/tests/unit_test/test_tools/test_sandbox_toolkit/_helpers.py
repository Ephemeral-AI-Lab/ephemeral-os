"""Sandbox toolkit test helpers."""

from __future__ import annotations

from typing import Any

from tools import BaseTool, ToolExecutionContextService, ToolResult, execute_tool_once


async def run_tool_safely(
    tool: BaseTool,
    raw_input: dict[str, Any],
    context: ToolExecutionContextService,
) -> ToolResult:
    async def _noop_emit(_event: Any) -> None:
        return None

    return await execute_tool_once(
        tool,
        raw_input,
        context,
        emit=_noop_emit,
        emit_started=False,
    )
