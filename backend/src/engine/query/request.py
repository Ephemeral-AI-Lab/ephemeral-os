"""Provider request construction and prompt-report recording for agent runs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from engine.query.provider_history import prepare_provider_messages
from message.agent_message_recorder import recorder_for_agent_run
from message.messages import ConversationMessage, TextBlock
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

    _record_initial_messages_once(context, messages)

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


def _record_initial_messages_once(
    context: QueryContext, messages: list[ConversationMessage]
) -> None:
    """Write the system prompt + every initial user message once.

    The recorder ignores repeated calls via its ``_initial_messages_recorded``
    flag, so this is safe on every turn. The audit recorder is reached via a
    module-level registry keyed by ``agent_run_id`` (see
    ``message.agent_message_recorder.register_recorder_for_agent_run``).

    For two-user-message launches the launcher seeds
    ``initial_messages=[context_message]`` and passes ``role_instruction``
    as the spawn prompt — we hand the recorder the additional seeded user
    rows so the on-disk transcript holds the full launch shape (system +
    user_msg_1 + user_msg_2) rather than just system + the last user row.
    """
    recorder = recorder_for_agent_run(context.run_id)
    if recorder is None:
        return
    last_user_prompt = _last_user_prompt_text(messages)
    if last_user_prompt is None:
        return
    seeded = _user_messages_before_last(messages)
    recorder.record_initial_messages(
        system_prompt=context.system_prompt,
        user_prompt=last_user_prompt,
        agent_name=context.agent_name,
        run_id=context.run_id,
        seeded_initial_messages=seeded,
    )


def _first_user_prompt_text(messages: list[ConversationMessage]) -> str | None:
    for message in messages:
        if message.role != "user":
            continue
        parts: list[str] = []
        for block in message.content:
            if isinstance(block, TextBlock):
                parts.append(block.text)
        return "".join(parts) if parts else ""
    return None


def _last_user_prompt_text(messages: list[ConversationMessage]) -> str | None:
    """Return the last contiguous user message's text in the prefix.

    The launcher path is: system + initial_messages[...] + spawn_prompt.
    Every entry is either a user message (text) or a follow-up that arrives
    after the first turn. We only care about the prefix of user messages
    before any assistant turn. We treat the LAST of those as the spawn
    prompt and everything before as seeded initial messages.
    """
    prefix: list[ConversationMessage] = []
    for message in messages:
        if message.role != "user":
            break
        prefix.append(message)
    if not prefix:
        return None
    last = prefix[-1]
    parts: list[str] = []
    for block in last.content:
        if isinstance(block, TextBlock):
            parts.append(block.text)
    return "".join(parts) if parts else ""


def _user_messages_before_last(
    messages: list[ConversationMessage],
) -> list[ConversationMessage]:
    """Return every seeded user message except the spawn-prompt one."""
    prefix: list[ConversationMessage] = []
    for message in messages:
        if message.role != "user":
            break
        prefix.append(message)
    return prefix[:-1]
