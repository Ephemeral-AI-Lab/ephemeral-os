"""Compatibility helper for direct tool execution."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tools.core.results import ToolResult

if TYPE_CHECKING:
    from tools.core.base import BaseTool
    from tools.core.context import ToolExecutionContextService


async def run_tool_safely(
    tool: BaseTool,
    raw_input: dict[str, Any],
    context: ToolExecutionContextService,
) -> ToolResult:
    """Validate input, execute *tool*, and normalise errors to a ``ToolResult``.

    Used by both the streaming executor and the background-dispatch path
    so validation and error framing stay consistent across the engine's
    tool invocation sites. ``asyncio.CancelledError`` is intentionally
    not caught — callers decide how to handle cancellation.
    """

    async def _noop_emit(_event: Any) -> None:
        return None

    from tools.core.tool_execution import execute_tool_once

    return await execute_tool_once(
        tool,
        raw_input,
        context,
        emit=_noop_emit,
        emit_started=False,
    )
