"""Hook-aware low-level tool execution helpers."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from message.stream_events import ToolExecutionStarted
from tools.core.base import (
    BaseTool,
    ToolExecutionContext,
    ToolResult,
    execute_tool_body,
    parse_tool_input,
    validate_tool_output,
)
from tools.core.hooks.outcomes import EmitStreamEvent
from tools.core.hooks.pipeline import run_post_hooks, run_pre_hooks


async def execute_tool_with_hooks(
    tool: BaseTool,
    raw_input: dict[str, Any],
    context: ToolExecutionContext,
    *,
    emit: EmitStreamEvent,
    emit_started: bool = True,
) -> ToolResult:
    """Validate input, run platform hooks, execute the tool, and validate output."""
    parsed = parse_tool_input(tool, raw_input)
    if parsed.error is not None:
        return parsed.error
    assert parsed.args is not None

    pre = await run_pre_hooks(tool.name, parsed.args, context, emit=emit)
    if pre.has_error:
        return ToolResult(
            output=f"pre-hook blocked {tool.name}: {pre.error_message}",
            is_error=True,
            metadata={"blocked_by": "pre_hook"},
        )

    effective_args = pre.tool_input
    if emit_started:
        await emit(
            ToolExecutionStarted(
                tool_name=tool.name,
                tool_input=effective_args.model_dump(mode="json"),
            )
        )

    result = await execute_tool_body(tool, effective_args, context)
    validated = validate_tool_output(tool, result)

    post = await run_post_hooks(tool.name, effective_args, context, validated, emit=emit)
    if post.has_error:
        return ToolResult(
            output=f"post-hook failed {tool.name}: {post.error_message}",
            is_error=True,
            metadata={
                **validated.metadata,
                "blocked_by": "post_hook",
                "original_tool_is_error": validated.is_error,
            },
        )
    if tool.is_terminal_tool and not validated.is_error:
        return replace(validated, does_terminate=True)
    return validated
