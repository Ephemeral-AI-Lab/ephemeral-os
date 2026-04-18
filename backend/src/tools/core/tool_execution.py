"""Tool execution logic — handles a single tool call end-to-end."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from message.messages import ToolResultBlock
from message.stream_events import StreamEvent
from tools.core.base import ExecutionMetadata, ToolExecutionContext
from tools.core.hooks.execution import execute_tool_with_hooks
from tools.core.runtime import merge_runtime_metadata

if TYPE_CHECKING:
    from engine.core.query import QueryContext
    from tools.core.hooks import EmitStreamEvent


def _build_budget_exceeded_error(
    tool_use_id: str,
    tool_call_limit: int,
) -> ToolResultBlock:
    return ToolResultBlock(
        tool_use_id=tool_use_id,
        content=(
            f"tool_call_limit exceeded: {tool_call_limit} tool "
            f"calls already used. The agent run will terminate after "
            f"this turn — wrap up and summarize your progress now to "
            f"preserve partial work."
        ),
        is_error=True,
    )


def _consume_tool_budget_or_reject(
    context: QueryContext,
    tool_name: str,
    tool_use_id: str,
) -> ToolResultBlock | None:
    if context.tool_call_limit is None:
        return None
    if context.tool_calls_used >= context.tool_call_limit:
        if tool_name in context.terminal_tools:
            return None
        return _build_budget_exceeded_error(tool_use_id, context.tool_call_limit)
    context.tool_calls_used += 1
    return None


async def execute_tool_call(
    context: QueryContext,
    tool_name: str,
    tool_use_id: str,
    tool_input: dict[str, object],
    extra_metadata: ExecutionMetadata | dict[str, Any] | None = None,
) -> ToolResultBlock:
    async def _noop_emit(event: StreamEvent) -> None:
        del event

    return await execute_tool_call_streaming(
        context,
        tool_name,
        tool_use_id,
        tool_input,
        extra_metadata=extra_metadata,
        emit=_noop_emit,
        emit_started=False,
    )


async def execute_tool_call_streaming(
    context: QueryContext,
    tool_name: str,
    tool_use_id: str,
    tool_input: dict[str, object],
    *,
    emit: "EmitStreamEvent",
    extra_metadata: ExecutionMetadata | dict[str, Any] | None = None,
    consume_budget: bool = True,
    emit_started: bool = True,
) -> ToolResultBlock:
    """Execute one tool call through the platform hook pipeline."""
    if consume_budget:
        budget_rejection = _consume_tool_budget_or_reject(context, tool_name, tool_use_id)
        if budget_rejection is not None:
            return budget_rejection

    tool = context.tool_registry.get(tool_name)
    if tool is None:
        return ToolResultBlock(
            tool_use_id=tool_use_id,
            content=f"Unknown tool: {tool_name}",
            is_error=True,
        )

    metadata = (
        context.tool_metadata.copy() if context.tool_metadata is not None else ExecutionMetadata()
    )
    metadata.tool_registry = context.tool_registry
    metadata.tool_id = tool_use_id
    if extra_metadata:
        metadata.update(extra_metadata)

    result = await execute_tool_with_hooks(
        tool,
        tool_input,
        ToolExecutionContext(cwd=context.cwd, metadata=metadata),
        emit=emit,
        emit_started=emit_started,
    )
    merge_runtime_metadata(
        original=context.tool_metadata, updated=metadata, result_metadata=result.metadata
    )
    if not result.is_error:
        from engine.runtime.tool_trace import record_tool_trace

        record_tool_trace(
            context.tool_metadata,
            tool_name,
            tool_input,
            tool_use_id=tool_use_id,
        )

    tool_result = ToolResultBlock(
        tool_use_id=tool_use_id,
        content=result.output,
        is_error=result.is_error,
        metadata=result.metadata,
    )
    return tool_result
