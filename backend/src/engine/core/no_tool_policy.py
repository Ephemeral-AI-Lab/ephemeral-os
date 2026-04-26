"""Policy for assistant turns that contain no tool calls."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from engine.core.turn_request import QueryTurnRequest, record_terminal_nudge
from engine.runtime.background_tasks import BackgroundTaskManager
from engine.runtime.background_tasks import (
    append_background_reminder,
    deliver_completed_background_task,
)
from message.messages import ConversationMessage
from message.stream_events import StreamEvent
from providers.types import UsageSnapshot

if TYPE_CHECKING:
    from engine.core.query import QueryContext


MAX_TERMINAL_NUDGE_RETRIES = 3
TERMINAL_NUDGE_BUDGET_BONUS = 10


@dataclass(frozen=True)
class NoToolTurnOutcome:
    should_continue: bool
    exit_text_response: bool = False
    events: list[tuple[StreamEvent, UsageSnapshot | None]] = field(default_factory=list)


def build_terminal_nudge_text(terminal_tools: Iterable[str], attempt: int) -> str:
    tool_list = ", ".join(sorted(terminal_tools))
    return (
        "[terminal-tool reminder] Your previous turn ended without a terminal tool. "
        "Your next assistant message must contain exactly one terminal "
        f"tool call: {tool_list}. Do not call non-terminal tools or add narration. "
        "If a terminal payload was rejected, fix only the reported schema issue "
        "and resubmit. "
        f"(nudge {attempt}/{MAX_TERMINAL_NUDGE_RETRIES})"
    )


async def handle_no_tool_turn(
    context: QueryContext,
    messages: list[ConversationMessage],
    *,
    background_manager: BackgroundTaskManager | None,
    turn: QueryTurnRequest,
) -> NoToolTurnOutcome:
    if (
        context.terminal_tools
        and context.terminal_nudge_retries_used < MAX_TERMINAL_NUDGE_RETRIES
    ):
        context.terminal_nudge_retries_used += 1
        if (
            context.tool_call_limit is not None
            and not context.terminal_nudge_budget_extended
        ):
            context.tool_call_limit += TERMINAL_NUDGE_BUDGET_BONUS
            context.terminal_nudge_budget_extended = True
        attempt = context.terminal_nudge_retries_used
        nudge_message = ConversationMessage.from_user_text(
            build_terminal_nudge_text(context.terminal_tools, attempt)
        )
        messages.append(nudge_message)
        record_terminal_nudge(turn, attempt, nudge_message)
        return NoToolTurnOutcome(should_continue=True)

    if background_manager is None or not background_manager.has_pending():
        return NoToolTurnOutcome(should_continue=False, exit_text_response=True)

    completed_task = await background_manager.wait_any(timeout=30)
    if completed_task is not None:
        event = deliver_completed_background_task(completed_task, messages)
        return NoToolTurnOutcome(should_continue=True, events=[(event, None)])

    append_background_reminder(background_manager, messages)
    return NoToolTurnOutcome(should_continue=True)
