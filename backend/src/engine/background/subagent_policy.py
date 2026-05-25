"""Subagent-specific background task policy."""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

DEFAULT_BACKGROUND_TASK_TYPE = "agent"
SUBAGENT_TASK_TYPE = "subagent"
EARLY_STOP_MODE = "early_stop"
EARLY_STOP_COMPLETION_MODE = "early_stopped"


class SubagentTrackedTask(Protocol):
    task_type: str
    stop_mode: str | None
    completion_mode: str | None
    progress_lines: list[str]
    asyncio_task: asyncio.Task[Any]


def mark_completion_mode_if_stopped(tracked: SubagentTrackedTask) -> None:
    if tracked.stop_mode == EARLY_STOP_MODE:
        tracked.completion_mode = EARLY_STOP_COMPLETION_MODE


def should_cancel_asyncio_task(tracked: SubagentTrackedTask) -> bool:
    return tracked.task_type != SUBAGENT_TASK_TYPE


async def request_subagent_early_stop(
    tracked: SubagentTrackedTask,
    *,
    reason: str = "",
) -> None:
    tracked.stop_mode = EARLY_STOP_MODE
    tracked.progress_lines = [f"Early stop requested{': ' + reason if reason else ''}"]
    # Give a freshly launched subagent one event-loop cycle to reach its first
    # cooperative await so cancellation can be salvaged into a partial result.
    await asyncio.sleep(0)
    tracked.asyncio_task.cancel()
    # Let trivial cancellation handlers and the task done-callback run before
    # status is reported back to the caller.
    await asyncio.sleep(0)


__all__ = [
    "DEFAULT_BACKGROUND_TASK_TYPE",
    "SUBAGENT_TASK_TYPE",
    "mark_completion_mode_if_stopped",
    "request_subagent_early_stop",
    "should_cancel_asyncio_task",
]
