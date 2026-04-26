"""Provider request construction and prompt-report recording for query turns."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from engine.core.provider_history import prepare_provider_messages
from message.messages import ConversationMessage
from prompt.prompt_report_recorder import PromptReportRecorder
from providers.types import ApiMessageRequest, UsageSnapshot
from tools.core.base import decorate_schemas_for_background

if TYPE_CHECKING:
    from engine.core.query import QueryContext


@dataclass(frozen=True)
class QueryTurnRequest:
    request: ApiMessageRequest
    prompt_report: PromptReportRecorder
    prompt_report_seq: int
    context_message: str


def prompt_report_recorder(context: QueryContext) -> PromptReportRecorder:
    if context.prompt_report_recorder is not None:
        return context.prompt_report_recorder
    metadata = context.tool_metadata
    context.prompt_report_recorder = PromptReportRecorder(
        metadata.get("prompt_report_messages_path") if metadata is not None else None,
        base_event=(
            {
                "agent_run_id": metadata.get("agent_run_id"),
                "agent": context.agent_name or metadata.get("agent_name"),
                "model": context.model,
            }
            if metadata is not None
            else {"agent": context.agent_name, "model": context.model}
        ),
    )
    return context.prompt_report_recorder


def build_query_turn_request(
    context: QueryContext,
    messages: list[ConversationMessage],
) -> QueryTurnRequest:
    provider_messages = prepare_provider_messages(messages)
    context_message = (context.user_context_message or "").strip()
    if context_message:
        provider_messages = [
            ConversationMessage.from_user_text(context_message),
            *provider_messages,
        ]

    prompt_report = prompt_report_recorder(context)
    prompt_report_seq = prompt_report.next_seq()
    tool_schemas = context.tool_registry.to_api_schema()
    if context.enable_background_tasks:
        tool_schemas = decorate_schemas_for_background(
            context.tool_registry,
            tool_schemas,
            terminal_tools=context.terminal_tools,
        )

    prompt_report.record(
        {
            "event": "llm_request",
            "seq": prompt_report_seq,
            "system_prompt": context.system_prompt,
            "user_context_message": context_message,
            "messages": [m.model_dump(mode="json") for m in provider_messages],
            "tools": tool_schemas,
        }
    )

    return QueryTurnRequest(
        request=ApiMessageRequest(
            model=context.model,
            messages=provider_messages,
            system_prompt=context.system_prompt,
            max_tokens=context.max_tokens,
            tools=tool_schemas,
        ),
        prompt_report=prompt_report,
        prompt_report_seq=prompt_report_seq,
        context_message=context_message,
    )


def record_assistant_turn(
    turn: QueryTurnRequest,
    message: ConversationMessage,
    usage: UsageSnapshot,
) -> None:
    turn.prompt_report.record(
        {
            "event": "assistant",
            "seq": turn.prompt_report_seq,
            "message": message.model_dump(mode="json"),
            "usage": usage.model_dump(mode="json"),
        }
    )


def record_terminal_nudge(
    turn: QueryTurnRequest,
    attempt: int,
    message: ConversationMessage,
) -> None:
    turn.prompt_report.record(
        {
            "event": "terminal_nudge",
            "seq": turn.prompt_report.next_seq(),
            "attempt": attempt,
            "message": message.model_dump(mode="json"),
        }
    )


def record_tool_result_message(
    turn: QueryTurnRequest,
    message: ConversationMessage,
) -> None:
    turn.prompt_report.record(
        {
            "event": "tool_result",
            "seq": turn.prompt_report_seq,
            "message": message.model_dump(mode="json"),
        }
    )


def record_hook_system_reminder(
    turn: QueryTurnRequest,
    message: ConversationMessage,
) -> None:
    turn.prompt_report.record(
        {
            "event": "hook_system_reminder",
            "seq": turn.prompt_report.next_seq(),
            "message": message.model_dump(mode="json"),
        }
    )
