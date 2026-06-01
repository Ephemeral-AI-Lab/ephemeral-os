"""Provider request construction and prompt-report recording for agent runs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from engine.query.provider_history import build_provider_messages
from message.agent_message_recorder import recorder_for_run
from message.message import Message, TextBlock
from prompt.prompt_report_recorder import PromptReportRecorder, recorder_for_context
from providers.types import MessageRequest

if TYPE_CHECKING:
    from engine.query.context import QueryContext


@dataclass(frozen=True)
class QueryRunRequest:
    request: MessageRequest
    prompt_report: PromptReportRecorder
    prompt_report_seq: int


def build_query_run_request(
    context: QueryContext,
    messages: list[Message],
) -> QueryRunRequest:
    provider_messages = build_provider_messages(messages)
    prompt_report = recorder_for_context(context)
    prompt_report_seq = prompt_report.next_seq()
    tool_schemas = context.tool_registry.to_api_schema()

    prompt_report.record_llm_request(
        seq=prompt_report_seq,
        system_prompt=context.system_prompt,
        messages=provider_messages,
        tools=tool_schemas,
    )

    _record_initial_messages_once(context, messages)

    return QueryRunRequest(
        request=MessageRequest(
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
    context: QueryContext, messages: list[Message]
) -> None:
    """Write the system prompt + every initial user message once.

    The recorder ignores repeated calls via its ``_initial_messages_recorded``
    flag, so this is safe on every turn. The audit recorder is reached via a
    module-level registry keyed by ``agent_run_id`` (see
    ``message.agent_message_recorder.register_recorder``).

    For two-user-message launches the launcher seeds
    ``initial_messages=[instruction]`` and passes task guidance as the
    spawn prompt — we hand the recorder the additional seeded user
    rows so the on-disk transcript holds the full launch shape (system +
    user_msg_1 + user_msg_2) rather than just system + the last user row.
    """
    recorder = recorder_for_run(context.agent_name, context.agent_run_id)
    if recorder is None:
        return
    initial_user_messages = _initial_user_message_prefix(messages)
    if not initial_user_messages:
        return
    spawn_prompt = _user_message_text(initial_user_messages[-1])
    recorder.record_initial_messages(
        system_prompt=context.system_prompt,
        user_prompt=spawn_prompt,
        agent_name=context.agent_name,
        run_id=context.agent_run_id,
        seeded_initial_messages=initial_user_messages[:-1],
    )


def _initial_user_message_prefix(
    messages: list[Message],
) -> list[Message]:
    """Return contiguous initial user messages before the first assistant turn.

    The launcher path is: system + initial_messages[...] + spawn_prompt.
    Every entry is either a user message (text) or a follow-up that arrives
    after the first turn. We only care about the prefix of user messages
    before any assistant turn. We treat the LAST of those as the spawn
    prompt and everything before as seeded initial messages.
    """
    prefix: list[Message] = []
    for message in messages:
        if message.role != "user":
            break
        prefix.append(message)
    return prefix


def _user_message_text(message: Message) -> str:
    return "".join(
        block.text for block in message.content if isinstance(block, TextBlock)
    )
