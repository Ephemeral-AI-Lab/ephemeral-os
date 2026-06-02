"""Core tool-aware query loop."""

from __future__ import annotations

import math
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, cast

from providers.types import UsageSnapshot
from message.message import Message
from message.events import (
    AssistantMessageCompleteEvent,
    StreamEvent,
    ToolExecutionCompletedEvent,
    ToolUseDeltaEvent,
)
from engine.tool_call.streaming import StreamingToolExecutor, defer_background_dispatch
from engine.tool_call.dispatch import dispatch_assistant_tools
from engine.query.request import (
    QueryRunRequest,
    build_query_run_request,
)
from engine.query.context import QueryContext, QueryExitReason
from engine.background.task_supervisor import BackgroundTaskSupervisor
from engine.tool_call.context import prepare_tool_execution_context
from notification import (
    SystemNotificationService,
    dispatch_rules,
    ensure_system_notification_service,
    flush_system_notification_events,
)
from tools import (
    BaseTool,
    ExecutionMetadata,
    ToolExecutionContextService,
    _count_tool_dispatch,
)


def terminal_submission_failed(context: QueryContext) -> bool:
    """True iff the agent has burned 1.5× its tool_call_limit without a
    terminal submission."""
    return (
        context.tool_calls_used + context.text_only_no_terminal_turns
        >= math.ceil(1.5 * context.tool_call_limit)
    )


def _terminal_not_submitted_message(context: QueryContext) -> str:
    return (
        f"Agent stopped: terminal tool not submitted. "
        f"tool_calls_used={context.tool_calls_used}, "
        f"text_only_no_terminal_turns={context.text_only_no_terminal_turns}, "
        f"tool_call_limit={context.tool_call_limit}, "
        f"hard_ceiling={math.ceil(1.5 * context.tool_call_limit)}."
    )


def _make_stream_dispatch_deferrer(
    context: QueryContext,
    background_tasks: BackgroundTaskSupervisor | None,
) -> Callable[[BaseTool | None, dict[str, Any] | None], bool]:
    """Build a per-stream `should_defer` predicate for `StreamingToolExecutor`.

    Terminal-capable runs defer every tool until the complete assistant message
    is available, so terminal-tool exclusivity is validated before any sibling
    tool body can run.
    """

    def _defer(tool_def: BaseTool | None, tool_input: dict[str, Any] | None) -> bool:
        if background_tasks is not None and defer_background_dispatch(tool_def, tool_input):
            return True
        if context.terminal_tools:
            return True
        return False

    return _defer


# ---------------------------------------------------------------------------
# Query loop
# ---------------------------------------------------------------------------


@dataclass
class _ProviderStreamAccumulator:
    """Mutable accumulator for one provider stream."""

    final_message: Message | None = None
    usage: UsageSnapshot = field(default_factory=UsageSnapshot)
    streamed_tool_use_ids: set[str] = field(default_factory=set)


def _prepare_query_loop_runtime(
    context: QueryContext,
) -> tuple[BackgroundTaskSupervisor | None, SystemNotificationService]:
    """One-time setup before issuing the provider request."""
    if context.tool_metadata is None:
        context.tool_metadata = ExecutionMetadata()
    elif not isinstance(context.tool_metadata, ExecutionMetadata):
        if not isinstance(context.tool_metadata, Mapping):
            raise TypeError(
                "tool_metadata must be ExecutionMetadata or Mapping, "
                f"got {type(context.tool_metadata).__name__}"
            )
        coerced = ExecutionMetadata()
        coerced.update(context.tool_metadata)
        context.tool_metadata = coerced

    notification_service = ensure_system_notification_service(context.tool_metadata)

    background_tasks: BackgroundTaskSupervisor | None = None
    if context.enable_background_tasks:
        background_tasks = BackgroundTaskSupervisor()
        context.tool_metadata.background_task_manager = background_tasks

    # Derive terminal tool names from the registry. Tools self-annotate via
    # ``is_terminal_tool=True``. The ``not pre-set`` guard lets test fixtures
    # construct ``QueryContext(terminal_tools={...})`` directly without
    # registering full tool implementations; in production this set is always
    # empty at this point and gets populated here.
    if not context.terminal_tools:
        context.terminal_tools = {
            tool.name
            for tool in context.tool_registry.list_tools()
            if tool.is_terminal_tool
        }

    return background_tasks, notification_service


async def _build_stream_executor(
    context: QueryContext,
    background_tasks: BackgroundTaskSupervisor | None,
    messages: list[Message],
) -> StreamingToolExecutor:
    """Build the streaming tool executor for this provider request."""
    metadata = (
        context.tool_metadata.copy()
        if context.tool_metadata is not None
        else ExecutionMetadata()
    ).with_overrides(conversation_messages=messages)
    if context.task_id:
        metadata.task_id = context.task_id
    execution_context = ToolExecutionContextService(
        cwd=context.cwd,
        services=metadata,
    )
    executor = StreamingToolExecutor(
        tool_registry=context.tool_registry,
        context=execution_context,
        should_defer=_make_stream_dispatch_deferrer(
            context,
            background_tasks=background_tasks,
        ),
    )
    await prepare_tool_execution_context(context, execution_context)
    return executor


async def _drain_background_completion_notifications(
    background_tasks: BackgroundTaskSupervisor | None,
    notification_service: SystemNotificationService,
) -> None:
    if background_tasks is None:
        return
    for text in background_tasks.collect_subagent_completion_notifications():
        await notification_service.notify_system(text)
    for text in await background_tasks.collect_command_session_completion_notifications():
        await notification_service.notify_system(text)


def _provider_event_source(
    context: QueryContext,
    run_request: QueryRunRequest,
) -> AsyncIterator[StreamEvent]:
    """Default event source: stream from the live provider ``api_client``.

    The sole site that issues a provider request. ``context.event_source``
    overrides this (mock harness) without any other loop change, so the mock
    path is byte-identical to production except for the event *content*.
    """
    return context.api_client.stream_message(run_request.request)


async def _consume_provider_stream(
    context: QueryContext,
    executor: StreamingToolExecutor,
    run_request: QueryRunRequest,
    state: _ProviderStreamAccumulator,
) -> AsyncIterator[tuple[StreamEvent, UsageSnapshot | None]]:
    """Consume the provider stream, populating ``state`` along the way."""
    source = context.event_source or _provider_event_source
    try:
        async for event in source(context, run_request):
            if isinstance(event, ToolUseDeltaEvent):
                state.streamed_tool_use_ids.add(event.tool_use_id)
                _count_tool_dispatch(context)
                executor.add_tool(event)
                for emitted in executor.get_events():
                    yield emitted, None
                for progress in executor.get_progress():
                    yield progress, None
                continue

            if isinstance(event, AssistantMessageCompleteEvent):
                state.final_message = event.message
                state.usage = event.usage
                continue

            yield event, None

        if state.final_message is None:
            raise RuntimeError(
                f"Model stream finished without a final message for model {context.model}. "
                "This may indicate a provider error, content-filter cutoff, or "
                "misconfigured API endpoint / authentication / model name."
            )
    except BaseException:
        executor.cancel_all()
        raise


async def _run_query_loop(
    context: QueryContext,
    messages: list[Message],
) -> AsyncIterator[tuple[StreamEvent, UsageSnapshot | None]]:
    background_tasks, notification_service = _prepare_query_loop_runtime(context)

    try:
        while True:
            executor = await _build_stream_executor(context, background_tasks, messages)

            # Evaluate notification rules and drain any reminders into the
            # transcript before building the next provider request, so newly-
            # fired reminders reach the model on this turn.
            await _drain_background_completion_notifications(
                background_tasks,
                notification_service,
            )
            if context.notification_rules:
                await dispatch_rules(
                    context.notification_rules,
                    messages,
                    context,
                    notification_service,
                )
            pending = notification_service.pop_pending_notifications()
            if pending:
                messages.append(Message(role="user", content=list(pending)))

            state = _ProviderStreamAccumulator()
            run_request = build_query_run_request(context, messages)
            async for event, event_usage in _consume_provider_stream(
                context, executor, run_request, state
            ):
                yield event, event_usage

            for progress in executor.get_progress():
                yield progress, None
            for emitted in executor.get_events():
                yield emitted, None

            final_message = state.final_message
            assert final_message is not None  # narrowed by _consume_provider_stream
            messages.append(final_message)
            run_request.prompt_report.record_assistant(
                seq=run_request.prompt_report_seq,
                message=final_message,
                usage=state.usage,
            )
            yield AssistantMessageCompleteEvent(
                message=final_message,
                usage=state.usage,
                agent_name=context.agent_name,
                agent_run_id=context.agent_run_id,
            ), state.usage

            if final_message.tool_uses:
                dispatch = await dispatch_assistant_tools(
                    context,
                    messages,
                    final_message,
                    executor,
                    streamed_tool_use_ids=state.streamed_tool_use_ids,
                    background_tasks=background_tasks,
                )
                for event in dispatch.events:
                    yield event, None
                tool_results = list(dispatch.tool_results)
                run_request.prompt_report.record_tool_results(
                    seq=run_request.prompt_report_seq,
                    tool_results=tool_results,
                )
                for event in flush_system_notification_events(notification_service):
                    yield event, None
                if dispatch.terminal_result is not None:
                    context.terminal_result = dispatch.terminal_result
                if tool_results:
                    messages.append(Message(role="user", content=list(tool_results)))

            if context.terminal_result is not None:
                context.exit_reason = QueryExitReason.TOOL_STOP
                if background_tasks is not None:
                    for text in await background_tasks.terminate_for_parent_exit():
                        await notification_service.notify_system(text)
                    for event in flush_system_notification_events(notification_service):
                        yield event, None
                break
            if not final_message.tool_uses:
                context.text_only_no_terminal_turns += 1
            if terminal_submission_failed(context):
                if background_tasks is not None:
                    await background_tasks.cancel_all()
                yield (
                    ToolExecutionCompletedEvent(
                        tool_name="",
                        output=_terminal_not_submitted_message(context),
                        is_error=True,
                    ),
                    None,
                )
                for event in flush_system_notification_events(notification_service):
                    yield event, None
                context.exit_reason = QueryExitReason.TERMINAL_NOT_SUBMITTED
                break
            # Otherwise: loop. terminal_call_reminder fires next iteration.
    finally:
        if background_tasks is not None and background_tasks.has_pending():
            await background_tasks.cancel_all()


async def run_query(
    context: QueryContext,
    messages: list[Message],
) -> tuple[list[Message], AsyncIterator[tuple[StreamEvent, UsageSnapshot | None]]]:
    from dataclasses import fields, is_dataclass, replace

    agent_name = context.agent_name
    agent_run_id = context.agent_run_id

    def _stamp(
        event: StreamEvent,
    ) -> StreamEvent:
        if not is_dataclass(event):
            return event
        if not (agent_name or agent_run_id):
            return event
        names = {f.name for f in fields(event)}
        updates: dict[str, str] = {}
        if "agent_name" in names and not getattr(event, "agent_name", ""):
            updates["agent_name"] = agent_name
        if "agent_run_id" in names and not getattr(event, "agent_run_id", ""):
            updates["agent_run_id"] = agent_run_id
        if not updates:
            return event
        return cast(StreamEvent, replace(cast(Any, event), **updates))

    async def _stamped(
        inner: AsyncIterator[tuple[StreamEvent, UsageSnapshot | None]],
    ) -> AsyncIterator[tuple[StreamEvent, UsageSnapshot | None]]:
        async for event, usage in inner:
            yield _stamp(event), usage

    return messages, _stamped(_run_query_loop(context, messages))
