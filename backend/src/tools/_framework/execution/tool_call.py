"""Tool execution logic — handles a single tool call end-to-end."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from message.messages import ConversationMessage
from message.messages import ToolResultBlock
from message.stream_events import StreamEvent, ToolExecutionStarted
from tools._framework.core.base import BaseTool
from tools._framework.core.context import ToolExecutionContextService
from tools._framework.execution.hook_pipeline import ToolHookExecutionPipeline
from tools._framework.core.results import ToolResult
from tools._framework.core.runtime import ExecutionMetadata
from tools._framework.core.validation import execute_tool_body, parse_tool_input, validate_tool_output

if TYPE_CHECKING:
    from engine.api import QueryContext


EmitStreamEvent = Callable[[StreamEvent], Awaitable[None]]


def _count_tool_dispatch(context: QueryContext) -> None:
    """Increment the per-run tool-call counter.

    Soft-limit signaling is delivered via the ``budget_overflow_reminder``
    notification rule; hard-failure is the loop's responsibility when
    ``overshoot_units > max_tolerance_after_max_tool_call``.
    """
    if context.tool_call_limit is not None:
        context.tool_calls_used += 1


async def execute_tool_call(
    context: QueryContext,
    tool_name: str,
    tool_use_id: str,
    tool_input: dict[str, object],
    extra_metadata: ExecutionMetadata | dict[str, Any] | None = None,
    conversation_messages: list[ConversationMessage] | None = None,
) -> ToolResultBlock:
    async def _noop_emit(event: StreamEvent) -> None:
        del event

    return await execute_tool_call_streaming(
        context,
        tool_name,
        tool_use_id,
        tool_input,
        extra_metadata=extra_metadata,
        conversation_messages=conversation_messages,
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
    conversation_messages: list[ConversationMessage] | None = None,
    consume_budget: bool = True,
    emit_started: bool = True,
) -> ToolResultBlock:
    """Execute one tool call and emit lifecycle events for the active stream."""
    if consume_budget:
        _count_tool_dispatch(context)

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
    if context.task_center_task_id:
        metadata.task_center_task_id = context.task_center_task_id
    if conversation_messages is not None:
        metadata = metadata.with_overrides(conversation_messages=conversation_messages)
    if extra_metadata:
        metadata.update(extra_metadata)

    result = await execute_tool_once(
        tool,
        tool_input,
        ToolExecutionContextService(cwd=context.cwd, services=metadata),
        emit=emit,
        emit_started=emit_started,
    )
    if not result.is_error:
        from tools._framework.execution.trace import record_tool_trace

        record_tool_trace(
            context.tool_metadata,
            tool_name,
            _trace_input_from_result(result, tool_input),
        )

    tool_result = ToolResultBlock(
        tool_use_id=tool_use_id,
        content=result.output,
        is_error=result.is_error,
        metadata=result.metadata,
        does_terminate=result.does_terminate,
    )
    return tool_result


def _trace_input_from_result(
    result: ToolResult,
    fallback: dict[str, object],
) -> dict[str, object]:
    raw = result.metadata.get("effective_tool_input")
    return raw if isinstance(raw, dict) else fallback


async def execute_tool_once(
    tool: BaseTool,
    raw_input: dict[str, Any],
    context: ToolExecutionContextService,
    *,
    emit: EmitStreamEvent,
    emit_started: bool = True,
) -> ToolResult:
    """Validate input, emit start, execute the tool, and validate output."""
    hook_pipeline = ToolHookExecutionPipeline(tool, context, emit)
    parsed = parse_tool_input(tool, raw_input)
    if parsed.error is not None:
        return parsed.error
    assert parsed.args is not None

    parsed_input, hook_failure = await hook_pipeline.run_pre_hooks(parsed.args)
    if hook_failure is not None:
        return hook_failure
    assert parsed_input is not None

    if emit_started:
        await emit(
            ToolExecutionStarted(
                tool_name=tool.name,
                tool_input=parsed_input.model_dump(mode="json"),
            )
        )

    result = await execute_tool_body(tool, parsed_input, context)
    validated = validate_tool_output(tool, result)
    hooked = await hook_pipeline.run_post_hooks(parsed_input, validated)
    final = hook_pipeline.finalize_result(hooked, effective_input=parsed_input)
    if tool.is_terminal_tool and not final.is_error:
        return replace(final, does_terminate=True)
    return final
