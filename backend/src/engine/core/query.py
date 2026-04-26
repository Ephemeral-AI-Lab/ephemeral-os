"""Core tool-aware query loop."""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from agents.types import AgentDefinition, ModeDefinition
from providers.types import (
    ApiCancelEvent,
    ApiMessageCompleteEvent,
    ApiTextDeltaEvent,
    ApiThinkingDeltaEvent,
    ApiToolUseDeltaEvent,
    SupportsStreamingMessages,
    UsageSnapshot,
)
from message.messages import ConversationMessage, ToolResultBlock
from message.stream_events import (
    AssistantTextDelta,
    AssistantTurnComplete,
    StreamEvent,
    ToolExecutionCompleted,
)
from engine.core.no_tool_policy import handle_no_tool_turn
from engine.core.streaming_executor import StreamingToolExecutor, defer_background_dispatch
from engine.core.tool_dispatch import dispatch_tool_turn
from engine.core.tool_results import (
    any_terminal_result,
    append_tool_result_history,
    apply_mode_transitions,
)
from engine.core.turn_request import build_query_turn_request, record_assistant_turn
from engine.runtime.background_tasks import BackgroundTaskManager
from engine.runtime.background_tasks import (
    append_background_reminder,
    deliver_completed_background_task,
)
from engine.runtime.tool_context import prepare_tool_execution_context
from notification.budget import build_budget_warning
from prompt.prompt_report_recorder import PromptReportRecorder
from tools.core.base import (
    ExecutionMetadata,
    ToolExecutionContextService,
    ToolRegistry,
)
from tools.core.tool_execution import (
    _consume_tool_budget_or_reject,
    evaluate_mode_gate,
)


logger = logging.getLogger(__name__)

CANCEL_PATTERN = re.compile(r'\[CANCEL:(\S+)(?:\s+reason="([^"]*)")?\]')


class QueryExitReason(str, Enum):
    """Why the query loop exited."""

    TEXT_RESPONSE = "text_response"      # no tool_uses in response
    TOOL_STOP = "tool_stop"              # terminal tool succeeded
    RESOURCE_LIMIT = "resource_limit"    # budget exhausted or max_tokens


@dataclass
class QueryContext:
    api_client: SupportsStreamingMessages
    tool_registry: ToolRegistry
    cwd: Path
    model: str
    system_prompt: str
    max_tokens: int
    agent_name: str = ""
    run_id: str = ""
    tool_call_limit: int | None = None
    tool_calls_used: int = 0
    last_budget_warning_remaining: int | None = None
    tool_metadata: ExecutionMetadata | None = None
    enable_background_tasks: bool = False
    user_context_message: str | None = None
    on_turn: Callable[[list[ConversationMessage]], None] | None = None
    terminal_tools: set[str] = field(default_factory=set)
    exit_reason: QueryExitReason | None = None
    prompt_report_recorder: PromptReportRecorder | None = None
    terminal_nudge_retries_used: int = 0
    terminal_nudge_budget_extended: bool = False
    # Agent mode typestate (see docs/architecture/agent-mode-system-v1.md).
    # ``agent_def`` is the bound AgentDefinition; ``active_mode`` is the
    # currently-active ModeDefinition. Both are populated at spawn time when
    # an agent_def is supplied. The dispatcher reads ``active_mode`` to gate
    # tool calls; the mode-entry tools mutate it via the ``mode_transition``
    # field on their ToolResult.
    agent_def: AgentDefinition | None = None
    active_mode: ModeDefinition | None = None


def _should_defer_stream_tool_dispatch(
    context: QueryContext,
    background_manager: BackgroundTaskManager | None,
) -> Callable[[Any | None, dict[str, Any] | None], bool]:
    exclusive_batch_seen = False

    def _defer(tool_def: Any | None, tool_input: dict[str, Any] | None) -> bool:
        nonlocal exclusive_batch_seen
        if background_manager is not None and defer_background_dispatch(tool_def, tool_input):
            return True
        if exclusive_batch_seen:
            return True
        tool_name = str(getattr(tool_def, "name", "") or "")
        # Terminal and mode-entry tools are batch-exclusive — they must not
        # execute mid-stream alongside siblings. Defer so validate_tool_batch
        # can enforce exclusivity after the full tool_uses list is known.
        is_terminal = bool(tool_name) and tool_name in context.terminal_tools
        is_mode_entry = bool(getattr(tool_def, "is_mode_entry_tool", False))
        if is_terminal or is_mode_entry:
            exclusive_batch_seen = True
            return True
        return False

    return _defer

# ---------------------------------------------------------------------------
# Query loop
# ---------------------------------------------------------------------------


async def _run_query_loop(
    context: QueryContext,
    messages: list[ConversationMessage],
) -> AsyncIterator[tuple[StreamEvent, UsageSnapshot | None]]:
    if context.tool_metadata is None:
        context.tool_metadata = ExecutionMetadata()
    elif not isinstance(context.tool_metadata, ExecutionMetadata):
        coerced = ExecutionMetadata()
        coerced.update(context.tool_metadata)
        context.tool_metadata = coerced

    background_manager: BackgroundTaskManager | None = None
    if context.enable_background_tasks:
        background_manager = BackgroundTaskManager()
        context.tool_metadata.background_task_manager = background_manager

    # Derive terminal tool names from the registry. Tools self-annotate via
    # ``is_terminal_tool=True``. The ``not pre-set`` guard lets test fixtures
    # construct ``QueryContext(terminal_tools={...})`` directly without
    # registering full tool implementations; in production this set is always
    # empty at this point and gets populated here.
    if not context.terminal_tools:
        context.terminal_tools = {
            tool.name
            for tool in context.tool_registry.list_tools()
            if getattr(tool, "is_terminal_tool", False)
        }

    while True:
        streamed_rejections: list[ToolResultBlock] = []
        budget_warning = build_budget_warning(context)
        if budget_warning is not None:
            history_msg, warning_event = budget_warning
            messages.append(history_msg)
            yield warning_event, None

        if background_manager is not None:
            for completed_task in background_manager.collect_completed():
                event = deliver_completed_background_task(completed_task, messages)
                yield event, None

            if background_manager.has_pending():
                append_background_reminder(background_manager, messages)

        if context.on_turn is not None:
            try:
                context.on_turn(messages)
            except Exception:
                logger.debug("on_turn callback failed", exc_info=True)

        execution_context = ToolExecutionContextService(
            cwd=context.cwd,
            services=context.tool_metadata,
        )
        executor = StreamingToolExecutor(
            tool_registry=context.tool_registry,
            context=execution_context,
            should_defer=_should_defer_stream_tool_dispatch(
                context,
                background_manager=background_manager,
            ),
        )

        await prepare_tool_execution_context(context, execution_context)

        final_message: ConversationMessage | None = None
        usage = UsageSnapshot()
        pending_cancel: dict[str, str] = {}
        streamed_tool_use_ids: set[str] = set()

        turn = build_query_turn_request(context, messages)
        async for event in context.api_client.stream_message(turn.request):
            if isinstance(event, ApiThinkingDeltaEvent):
                # Provider thinking arrives incrementally, but the engine exposes
                # it only on the completed assistant message.
                continue

            if isinstance(event, ApiTextDeltaEvent):
                if match := CANCEL_PATTERN.search(event.text):
                    tool_id, reason = match.groups()
                    pending_cancel[tool_id] = reason or "Cancelled by LLM"
                yield AssistantTextDelta(text=event.text), None
                continue

            if isinstance(event, ApiToolUseDeltaEvent):
                streamed_tool_use_ids.add(event.id)
                mode_rejection = evaluate_mode_gate(
                    context.active_mode,
                    event.name,
                    event.id,
                )
                if mode_rejection is not None:
                    streamed_rejections.append(mode_rejection)
                    yield (
                        ToolExecutionCompleted(
                            tool_name=event.name,
                            output=mode_rejection.content,
                            is_error=True,
                            tool_id=event.id,
                        ),
                        None,
                    )
                    continue
                budget_rejection = _consume_tool_budget_or_reject(
                    context,
                    event.name,
                    event.id,
                )
                if budget_rejection is not None:
                    streamed_rejections.append(budget_rejection)
                    yield (
                        ToolExecutionCompleted(
                            tool_name=event.name,
                            output=budget_rejection.content,
                            is_error=True,
                            tool_id=event.id,
                        ),
                        None,
                    )
                    continue
                executor.add_tool(event)
                for emitted in executor.get_events():
                    yield emitted, None
                for progress in executor.get_progress():
                    yield progress, None
                continue

            if isinstance(event, ApiCancelEvent):
                executor.cancel(event.tool_id, event.reason)
                continue

            if isinstance(event, ApiMessageCompleteEvent):
                final_message = event.message
                usage = event.usage

        if final_message is None:
            raise RuntimeError(
                f"Model stream finished without a final message for model {context.model}. "
                "Check that the API endpoint, authentication, and model name are correct."
            )

        for tool_id, reason in pending_cancel.items():
            executor.cancel(tool_id, reason)

        for progress in executor.get_progress():
            yield progress, None
        for emitted in executor.get_events():
            yield emitted, None

        messages.append(final_message)
        record_assistant_turn(turn, final_message, usage)
        yield AssistantTurnComplete(message=final_message, usage=usage), usage

        if not final_message.tool_uses:
            outcome = await handle_no_tool_turn(
                context,
                messages,
                background_manager=background_manager,
                turn=turn,
            )
            for event, event_usage in outcome.events:
                yield event, event_usage
            if outcome.exit_text_response:
                context.exit_reason = QueryExitReason.TEXT_RESPONSE
                return
            continue

        dispatch = await dispatch_tool_turn(
            context,
            final_message,
            executor,
            streamed_rejections=streamed_rejections,
            streamed_tool_use_ids=streamed_tool_use_ids,
            background_manager=background_manager,
        )
        for event, event_usage in dispatch.events:
            yield event, event_usage

        tool_results = dispatch.tool_results
        append_tool_result_history(messages, tool_results, turn=turn)
        apply_mode_transitions(context, tool_results)

        # Check for a successful terminal tool. A rejected terminal call
        # is feedback for the next model turn, not a completed terminal result.
        if any_terminal_result(tool_results):
            context.exit_reason = QueryExitReason.TOOL_STOP
            return

        if (
            context.tool_call_limit is not None
            and context.tool_calls_used >= context.tool_call_limit
        ):
            context.exit_reason = QueryExitReason.RESOURCE_LIMIT
            if background_manager is not None:
                await background_manager.cancel_all()
            yield (
                ToolExecutionCompleted(
                    tool_name="",
                    output=f"Agent stopped: tool_call_limit ({context.tool_call_limit}) exceeded.",
                    is_error=True,
                ),
                None,
            )
            return


async def run_query(
    context: QueryContext,
    messages: list[ConversationMessage],
) -> tuple[list[ConversationMessage], AsyncIterator[tuple[StreamEvent, UsageSnapshot | None]]]:
    from dataclasses import fields, is_dataclass, replace

    agent_name = context.agent_name
    run_id = context.run_id

    def _stamp(
        event: StreamEvent,
    ) -> StreamEvent:
        if not is_dataclass(event):
            return event
        if not (agent_name or run_id):
            return event
        names = {f.name for f in fields(event)}
        updates: dict[str, str] = {}
        if "agent_name" in names and not getattr(event, "agent_name", ""):
            updates["agent_name"] = agent_name
        if "run_id" in names and not getattr(event, "run_id", ""):
            updates["run_id"] = run_id
        if not updates:
            return event
        return replace(event, **updates)

    async def _stamped(
        inner: AsyncIterator[tuple[StreamEvent, UsageSnapshot | None]],
    ) -> AsyncIterator[tuple[StreamEvent, UsageSnapshot | None]]:
        async for event, usage in inner:
            yield _stamp(event), usage

    return messages, _stamped(_run_query_loop(context, messages))
