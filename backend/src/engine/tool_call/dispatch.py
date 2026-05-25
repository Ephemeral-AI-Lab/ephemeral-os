"""Tool dispatch coordination for assistant responses."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from engine.tool_call.streaming import StreamingToolExecutor
from engine.background.dispatch import dispatch_background_tool_call
from engine.background.task_supervisor import BackgroundTaskSupervisor
from message.messages import ConversationMessage, ToolResultBlock, ToolUseBlock
from message.stream_events import (
    StreamEvent,
    ToolExecutionCancelled,
    ToolExecutionCompleted,
)
from tools import ToolResult, execute_tool_call_streaming

if TYPE_CHECKING:
    from engine.query.context import QueryContext


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AssistantToolDispatchOutcome:
    tool_results: list[ToolResultBlock]
    terminal_result: ToolResult | None = None
    events: list[StreamEvent] = field(default_factory=list)


def _tool_result_block_from_completion(
    completed: ToolExecutionCompleted,
) -> ToolResultBlock:
    return ToolResultBlock(
        tool_use_id=completed.tool_id,
        content=completed.output,
        is_error=completed.is_error,
        metadata=dict(completed.metadata or {}),
        does_terminate=completed.does_terminate,
    )


def _tool_result_block_from_cancellation(
    cancelled: ToolExecutionCancelled,
) -> ToolResultBlock:
    return ToolResultBlock(
        tool_use_id=cancelled.tool_id,
        content=f"[CANCELLED] {cancelled.reason}",
        is_error=True,
    )


def _completion_event_from_tool_result_block(
    tool_call: ToolUseBlock,
    result: ToolResultBlock,
) -> ToolExecutionCompleted:
    return ToolExecutionCompleted(
        tool_name=tool_call.name,
        output=result.content,
        is_error=result.is_error,
        tool_id=tool_call.id,
        metadata=dict(result.metadata or {}),
        does_terminate=result.does_terminate,
    )


def _first_terminal_tool_result(
    tool_results: list[ToolResultBlock],
) -> ToolResult | None:
    for result in tool_results:
        if not result.does_terminate:
            continue
        return ToolResult(
            output=str(result.content),
            is_error=result.is_error,
            metadata=dict(result.metadata or {}),
            does_terminate=True,
        )
    return None


def _validate_tool_batch(
    context: QueryContext,
    tool_calls: list[ToolUseBlock],
) -> list[ToolResultBlock] | None:
    if not tool_calls or len(tool_calls) <= 1:
        return None

    terminal_in_batch = [
        tool_call for tool_call in tool_calls if tool_call.name in context.terminal_tools
    ]
    if not terminal_in_batch:
        return None

    flagged_names = ", ".join(
        sorted({f"`{tool_call.name}`" for tool_call in terminal_in_batch})
    )
    called_names = ", ".join(f"`{tool_call.name}`" for tool_call in tool_calls)
    message = (
        f"Terminal tool {flagged_names} must be called alone. "
        f"This response batched it with other tools: {called_names}. "
        f"No tool in this batch executed. "
        f"Resubmit with only the exclusive tool in its own final batch."
    )
    return [
        ToolResultBlock(tool_use_id=str(tool_call.id), content=message, is_error=True)
        for tool_call in tool_calls
    ]


def _record_tool_batch_rejection(
    context: QueryContext,
    tool_calls: list[ToolUseBlock],
    tool_results: list[ToolResultBlock],
) -> list[StreamEvent] | None:
    batch_rejection = _validate_tool_batch(context, tool_calls)
    if batch_rejection is None:
        return None
    tool_results.extend(batch_rejection)
    return [
        _completion_event_from_tool_result_block(tool_call, result)
        for tool_call, result in zip(tool_calls, batch_rejection, strict=True)
    ]


async def dispatch_assistant_tools(
    context: QueryContext,
    messages: list[ConversationMessage],
    final_message: ConversationMessage,
    executor: StreamingToolExecutor,
    *,
    streamed_tool_use_ids: set[str],
    background_tasks: BackgroundTaskSupervisor | None,
) -> AssistantToolDispatchOutcome:
    events: list[StreamEvent] = []
    tool_results: list[ToolResultBlock] = []

    remaining_events = await executor.get_remaining()
    events.extend(executor.get_events())
    for completed in remaining_events:
        if isinstance(completed, ToolExecutionCompleted):
            tool_results.append(_tool_result_block_from_completion(completed))
            events.append(completed)
        elif isinstance(completed, ToolExecutionCancelled):
            tool_results.append(_tool_result_block_from_cancellation(completed))
            events.append(completed)

    rejection_events = _record_tool_batch_rejection(
        context,
        final_message.tool_uses,
        tool_results,
    )
    if rejection_events is not None:
        executor.cancel_all()
        events.extend(rejection_events)
        return AssistantToolDispatchOutcome(
            tool_results=tool_results,
            terminal_result=_first_terminal_tool_result(tool_results),
            events=events,
        )

    resolved_ids = {result.tool_use_id for result in tool_results if result.tool_use_id}
    pending_tool_calls = [
        tool_call
        for tool_call in final_message.tool_uses
        if tool_call.id not in resolved_ids
    ]
    if pending_tool_calls:
        events.extend(
            await _dispatch_deferred_tool_calls(
                context,
                messages,
                pending_tool_calls,
                streamed_tool_use_ids=streamed_tool_use_ids,
                background_tasks=background_tasks,
                tool_results=tool_results,
            )
        )

    return AssistantToolDispatchOutcome(
        tool_results=tool_results,
        terminal_result=_first_terminal_tool_result(tool_results),
        events=events,
    )


async def _dispatch_deferred_tool_calls(
    context: QueryContext,
    messages: list[ConversationMessage],
    tool_calls: list[ToolUseBlock],
    *,
    streamed_tool_use_ids: set[str],
    background_tasks: BackgroundTaskSupervisor | None,
    tool_results: list[ToolResultBlock],
) -> list[StreamEvent]:
    events: list[StreamEvent] = []
    rejection_events = _record_tool_batch_rejection(context, tool_calls, tool_results)
    if rejection_events is not None:
        return rejection_events

    foreground_tool_calls: list[ToolUseBlock] = []
    for tool_call in tool_calls:
        tool_def = context.tool_registry.get(tool_call.name)
        requires_background = getattr(tool_def, "background", "forbidden") == "always"
        should_run_in_background = (
            (tool_call.input.get("background", False) or requires_background)
            if background_tasks
            else False
        )

        if should_run_in_background:
            assert background_tasks is not None
            events.extend(
                dispatch_background_tool_call(
                    context,
                    messages,
                    background_tasks,
                    tool_call,
                    tool_results,
                )
            )
        else:
            foreground_tool_calls.append(tool_call)

    if len(foreground_tool_calls) == 1:
        events.extend(
            await _dispatch_single_foreground_tool(
                context,
                messages,
                foreground_tool_calls[0],
                streamed_tool_use_ids=streamed_tool_use_ids,
                tool_results=tool_results,
            )
        )
    elif foreground_tool_calls:
        events.extend(
            await _dispatch_many_foreground_tools(
                context,
                messages,
                foreground_tool_calls,
                streamed_tool_use_ids=streamed_tool_use_ids,
                tool_results=tool_results,
            )
        )
    return events


async def _dispatch_single_foreground_tool(
    context: QueryContext,
    messages: list[ConversationMessage],
    tool_call: ToolUseBlock,
    *,
    streamed_tool_use_ids: set[str],
    tool_results: list[ToolResultBlock],
) -> list[StreamEvent]:
    emitted_events: list[StreamEvent] = []

    async def emit(event: StreamEvent) -> None:
        emitted_events.append(event)

    result = await execute_tool_call_streaming(
        context,
        tool_call.name,
        tool_call.id,
        tool_call.input,
        emit=emit,
        conversation_messages=messages,
        consume_budget=tool_call.id not in streamed_tool_use_ids,
    )
    tool_results.append(result)
    events: list[StreamEvent] = list(emitted_events)
    events.append(_completion_event_from_tool_result_block(tool_call, result))
    return events


async def _dispatch_many_foreground_tools(
    context: QueryContext,
    messages: list[ConversationMessage],
    foreground_tool_calls: list[ToolUseBlock],
    *,
    streamed_tool_use_ids: set[str],
    tool_results: list[ToolResultBlock],
) -> list[StreamEvent]:
    queue: asyncio.Queue[StreamEvent | tuple[ToolUseBlock, ToolResultBlock]] = asyncio.Queue()
    events: list[StreamEvent] = []

    async def run_foreground_tool(tool_call: ToolUseBlock) -> None:
        async def emit(event: StreamEvent) -> None:
            await queue.put(event)

        try:
            result = await execute_tool_call_streaming(
                context,
                tool_call.name,
                tool_call.id,
                tool_call.input,
                emit=emit,
                conversation_messages=messages,
                consume_budget=tool_call.id not in streamed_tool_use_ids,
            )
        except Exception as exc:
            logger.exception(
                "Foreground tool dispatch failed: tool_id=%s tool_name=%s",
                tool_call.id,
                tool_call.name,
            )
            result = ToolResultBlock(
                tool_use_id=tool_call.id,
                content=f"Tool execution failed: {exc}",
                is_error=True,
            )
        await queue.put((tool_call, result))

    tasks = [
        asyncio.create_task(run_foreground_tool(tool_call))
        for tool_call in foreground_tool_calls
    ]
    remaining = len(tasks)
    while remaining:
        item = await queue.get()
        if isinstance(item, tuple):
            tool_call, result = item
            tool_results.append(result)
            remaining -= 1
            events.append(_completion_event_from_tool_result_block(tool_call, result))
        else:
            events.append(item)
    await asyncio.gather(*tasks, return_exceptions=True)
    return events
