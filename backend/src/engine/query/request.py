"""Provider request construction and prompt-report recording for agent runs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from engine.query.provider_history import prepare_provider_messages
from message.messages import ConversationMessage
from prompt.prompt_report_recorder import PromptReportRecorder, recorder_for_context
from providers.types import ApiMessageRequest
from tools import decorate_schemas_for_background

if TYPE_CHECKING:
    from engine.query.context import QueryContext


@dataclass(frozen=True)
class QueryRunRequest:
    request: ApiMessageRequest
    prompt_report: PromptReportRecorder
    prompt_report_seq: int


def build_query_run_request(
    context: QueryContext,
    messages: list[ConversationMessage],
) -> QueryRunRequest:
    provider_messages = prepare_provider_messages(messages)
    prompt_report = recorder_for_context(context)
    prompt_report_seq = prompt_report.next_seq()
    tool_schemas = context.tool_registry.to_api_schema()
    if context.enable_background_tasks:
        tool_schemas = decorate_schemas_for_background(
            context.tool_registry,
            tool_schemas,
            terminal_tools=context.terminal_tools,
        )

    prompt_report.record_llm_request(
        seq=prompt_report_seq,
        system_prompt=context.system_prompt,
        messages=provider_messages,
        tools=tool_schemas,
    )

    return QueryRunRequest(
        request=ApiMessageRequest(
            model=context.model,
            messages=provider_messages,
            system_prompt=context.system_prompt,
            max_tokens=context.max_tokens,
            tools=tool_schemas,
        ),
        prompt_report=prompt_report,
        prompt_report_seq=prompt_report_seq,
    )
