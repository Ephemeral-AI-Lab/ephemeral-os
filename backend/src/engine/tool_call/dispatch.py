"""Tool dispatch coordination for assistant responses."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from engine.tool_call.streaming import StreamingToolExecutor
from engine.background.dispatch import launch_and_collect_bg_events
from engine.background.manager import BackgroundTaskManager
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
class ToolDispatchResult:
    tool_results: list[ToolResultBlock]
    terminal_result: ToolResult | None = None
    events: list[StreamEvent] = field(default_factory=list)


def _result_from_completed(completed: ToolExecutionCompleted) -> ToolResultBlock:
    return ToolResultBlock(
        tool_use_id=completed.tool_id,
        content=completed.output,
        is_error=completed.is_error,
        metadata=dict(completed.metadata or {}),
        does_terminate=completed.does_terminate,
    )


def _result_from_cancelled(completed: ToolExecutionCancelled) -> ToolResultBlock:
    return ToolResultBlock(
        tool_use_id=completed.tool_id,
        content=f"[CANCELLED] {completed.reason}",
        is_error=True,
    )


def _terminal_result_from_tool_results(
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


def _assign_missing_tool_result_ids(
    tool_results: list[ToolResultBlock],
    tool_uses: list[ToolUseBlock],
) -> None:
    assigned_ids: set[str] = {tr.tool_use_id for tr in tool_results if tr.tool_use_id}
    unassigned_ids = [tu.id for tu in tool_uses if tu.id not in assigned_ids]
    for result in tool_results:
        if not result.tool_use_id and unassigned_ids:
            result.tool_use_id = unassigned_ids.pop(0)


def _reject_tool_batch(
    tool_calls: list[ToolUseBlock],
    message: str,
) -> list[ToolResultBlock]:
    return [
        ToolResultBlock(tool_use_id=str(tc.id), content=message, is_error=True)
        for tc in tool_calls
    ]


def _validate_tool_batch(
    context: QueryContext,
    tool_calls: list[ToolUseBlock],
) -> list[ToolResultBlock] | None:
    if not tool_calls or len(tool_calls) <= 1:
        return None

    terminal_in_batch = [
        tc for tc in tool_calls if tc.name in context.terminal_tools
    ]
    if not terminal_in_batch:
        return None

    flagged_names = ", ".join(sorted({f"`{tc.name}`" for tc in terminal_in_batch}))
    called_names = ", ".join(f"`{tc.name}`" for tc in tool_calls)
    message = (
        f"Terminal tool {flagged_names} must be called alone. "
        f"This response batched it with other tools: {called_names}. "
        f"No tool in this batch executed. "
        f"Resubmit with only the exclusive tool in its own final batch."
    )
    return _reject_tool_batch(tool_calls, message=message)


async def dispatch_assistant_tools(
    context: QueryContext,
    messages: list[ConversationMessage],
    final_message: ConversationMessage,
    executor: StreamingToolExecutor,
    *,
    streamed_tool_use_ids: set[str],
    background_manager: BackgroundTaskManager | None,
) -> ToolDispatchResult:
    events: list[StreamEvent] = []
    tool_results: list[ToolResultBlock] = []

    remaining_events = await executor.get_remaining()
    events.extend(executor.get_events())
    for completed in remaining_events:
        if isinstance(completed, ToolExecutionCompleted):
            tool_results.append(_result_from_completed(completed))
            events.append(completed)
        elif isinstance(completed, ToolExecutionCancelled):
            tool_results.append(_result_from_cancelled(completed))
            events.append(completed)

    batch_rejection = _validate_tool_batch(context, final_message.tool_uses)
    if batch_rejection is not None:
        executor.cancel_all()
        tool_results.extend(batch_rejection)
        for tc, result in zip(final_message.tool_uses, batch_rejection, strict=True):
            events.append(
                ToolExecutionCompleted(
                    tool_name=tc.name,
                    output=result.content,
                    is_error=result.is_error,
                    tool_id=tc.id,
                    metadata=dict(result.metadata or {}),
                    does_terminate=result.does_terminate,
                )
            )
        _assign_missing_tool_result_ids(tool_results, final_message.tool_uses)
        return ToolDispatchResult(
            tool_results=tool_results,
            terminal_result=_terminal_result_from_tool_results(tool_results),
            events=events,
        )

    resolved_ids = {result.tool_use_id for result in tool_results if result.tool_use_id}
    pending_calls = [tc for tc in final_message.tool_uses if tc.id not in resolved_ids]
    if pending_calls:
        events.extend(
            await _dispatch_deferred_tool_calls(
                context,
                messages,
                pending_calls,
                streamed_tool_use_ids=streamed_tool_use_ids,
                background_manager=background_manager,
                tool_results=tool_results,
            )
        )

    _assign_missing_tool_result_ids(tool_results, final_message.tool_uses)
    return ToolDispatchResult(
        tool_results=tool_results,
        terminal_result=_terminal_result_from_tool_results(tool_results),
        events=events,
    )


async def _dispatch_deferred_tool_calls(
    context: QueryContext,
    messages: list[ConversationMessage],
    tool_calls: list[ToolUseBlock],
    *,
    streamed_tool_use_ids: set[str],
    background_manager: BackgroundTaskManager | None,
    tool_results: list[ToolResultBlock],
) -> list[StreamEvent]:
    events: list[StreamEvent] = []
    batch_rejection = _validate_tool_batch(context, tool_calls)
    if batch_rejection is not None:
        tool_results.extend(batch_rejection)
        for tc, result in zip(tool_calls, batch_rejection, strict=True):
            events.append(
                ToolExecutionCompleted(
                    tool_name=tc.name,
                    output=result.content,
                    is_error=result.is_error,
                    tool_id=tc.id,
                    metadata=dict(result.metadata or {}),
                    does_terminate=result.does_terminate,
                )
            )
        return events

    foreground_calls: list[ToolUseBlock] = []
    for tc in tool_calls:
        tool_def_for_check = context.tool_registry.get(tc.name)
        force_bg = getattr(tool_def_for_check, "background", "forbidden") == "always"
        is_background = (
            (tc.input.get("background", False) or force_bg)
            if background_manager
            else False
        )

        if is_background:
            assert background_manager is not None
            events.extend(
                launch_and_collect_bg_events(
                    context,
                    messages,
                    background_manager,
                    tc,
                    tool_results,
                )
            )
        else:
            foreground_calls.append(tc)

    if len(foreground_calls) == 1:
        events.extend(
            await _dispatch_single_foreground_tool(
                context,
                messages,
                foreground_calls[0],
                streamed_tool_use_ids=streamed_tool_use_ids,
                tool_results=tool_results,
            )
        )
    elif foreground_calls:
        events.extend(
            await _dispatch_many_foreground_tools(
                context,
                messages,
                foreground_calls,
                streamed_tool_use_ids=streamed_tool_use_ids,
                tool_results=tool_results,
            )
        )
    return events


async def _dispatch_single_foreground_tool(
    context: QueryContext,
    messages: list[ConversationMessage],
    tc: ToolUseBlock,
    *,
    streamed_tool_use_ids: set[str],
    tool_results: list[ToolResultBlock],
) -> list[StreamEvent]:
    emitted_events: list[StreamEvent] = []

    async def emit(event: StreamEvent) -> None:
        emitted_events.append(event)

    result = await execute_tool_call_streaming(
        context,
        tc.name,
        tc.id,
        tc.input,
        emit=emit,
        conversation_messages=messages,
        consume_budget=tc.id not in streamed_tool_use_ids,
    )
    tool_results.append(result)
    events: list[StreamEvent] = list(emitted_events)
    events.append(
        ToolExecutionCompleted(
            tool_name=tc.name,
            output=result.content,
            is_error=result.is_error,
            tool_id=tc.id,
            metadata=dict(result.metadata or {}),
            does_terminate=result.does_terminate,
        )
    )
    return events


async def _dispatch_many_foreground_tools(
    context: QueryContext,
    messages: list[ConversationMessage],
    foreground_calls: list[ToolUseBlock],
    *,
    streamed_tool_use_ids: set[str],
    tool_results: list[ToolResultBlock],
) -> list[StreamEvent]:
    queue: asyncio.Queue[StreamEvent | tuple[ToolUseBlock, ToolResultBlock]] = asyncio.Queue()
    events: list[StreamEvent] = []

    async def run_foreground(tc: ToolUseBlock) -> None:
        async def emit(event: StreamEvent) -> None:
            await queue.put(event)

        try:
            result = await execute_tool_call_streaming(
                context,
                tc.name,
                tc.id,
                tc.input,
                emit=emit,
                conversation_messages=messages,
                consume_budget=tc.id not in streamed_tool_use_ids,
            )
        except Exception as exc:
            logger.exception(
                "Foreground tool dispatch failed: tool_id=%s tool_name=%s",
                tc.id,
                tc.name,
            )
            result = ToolResultBlock(
                tool_use_id=tc.id,
                content=f"Tool execution failed: {exc}",
                is_error=True,
            )
        await queue.put((tc, result))

    tasks = [asyncio.create_task(run_foreground(tc)) for tc in foreground_calls]
    remaining = len(tasks)
    while remaining:
        item = await queue.get()
        if isinstance(item, tuple):
            tc, result = item
            tool_results.append(result)
            remaining -= 1
            events.append(
                ToolExecutionCompleted(
                    tool_name=tc.name,
                    output=result.content,
                    is_error=result.is_error,
                    tool_id=tc.id,
                    metadata=dict(result.metadata or {}),
                    does_terminate=result.does_terminate,
                )
            )
        else:
            events.append(item)
    await asyncio.gather(*tasks, return_exceptions=True)
    return events
