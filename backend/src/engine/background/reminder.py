"""Provider-visible reminders for running background tasks."""

from __future__ import annotations

import time

from engine.background.manager import BackgroundTaskManager
from message.messages import BackgroundTaskStateBlock, ContentBlock, ConversationMessage


def build_background_reminder(
    background_manager: BackgroundTaskManager,
) -> ConversationMessage | None:
    """Build a single durable user message summarising live background tasks.

    Returns ``None`` if no tasks are running. The returned message is a
    regular ``ConversationMessage`` and is appended to *messages*
    so the user and subsequent provider-history preparation can see it. It is NOT
    a separate ephemeral concept — once appended, it lives in history.

    Calling this advances the per-task reminder cursor via
    :meth:`BackgroundTaskManager.get_reminder_diff`, so each call yields
    only progress lines that have appeared since the previous reminder.
    """
    pending = list(background_manager.iter_running())
    if not pending:
        return None

    content: list[ContentBlock] = []
    for t in pending:
        elapsed = time.monotonic() - t.started_at
        new_lines, since = background_manager.get_reminder_diff(t.task_id)
        if new_lines:
            text = f"Running for {elapsed:.0f}s\nNew output (last {len(new_lines)} lines):\n"
            text += "\n".join(new_lines)
        else:
            text = f"Running for {elapsed:.0f}s\nNo new output in the last {since:.0f}s"
        text += (
            "\nKeep working on any other ready analysis or tool tasks first. "
            "Only wait when this background task is the remaining blocker. "
            "Do not recheck task ids after a terminal status."
        )
        content.append(
            BackgroundTaskStateBlock(
                task_id=t.task_id,
                tool_name=t.tool_name,
                task_type=t.task_type,
                status="running",
                source="engine_progress",
                text=text,
                agent_run_id=t.agent_run_id,
            )
        )

    return ConversationMessage(role="user", content=content)


def append_background_reminder(
    background_manager: BackgroundTaskManager,
    messages: list[ConversationMessage],
) -> bool:
    """Append a background reminder message to history.

    Returns ``False`` when no reminder is produced (no running tasks).
    """
    reminder_msg = build_background_reminder(background_manager)
    if reminder_msg is None:
        return False
    messages.append(reminder_msg)
    return True
