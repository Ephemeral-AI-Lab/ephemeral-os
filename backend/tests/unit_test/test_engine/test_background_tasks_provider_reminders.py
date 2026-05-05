"""Tests provider-backed reminder diffs for BackgroundTaskManager."""

from __future__ import annotations

import asyncio

from engine.runtime.background_tasks import BackgroundTaskManager, append_background_reminder
from tools.core.base import ToolResult


async def _slow_tool() -> ToolResult:
    await asyncio.sleep(5)
    return ToolResult(output="done")


async def test_get_reminder_diff_uses_progress_provider_deltas() -> None:
    mgr = BackgroundTaskManager()
    task_id = mgr.next_alias()
    snapshots = [
        "A: [text] first",
        "A: [text] first\nA: [text] second",
    ]
    idx = 0

    def provider(_: int) -> str:
        return snapshots[idx]

    mgr.launch(task_id, "run_subagent", {}, _slow_tool())
    mgr.set_progress_provider(task_id, provider)

    try:
        first_lines, _ = mgr.get_reminder_diff(task_id)
        assert first_lines == ["A: [text] first"]

        idx = 1
        second_lines, _ = mgr.get_reminder_diff(task_id)
        assert second_lines == ["A: [text] second"]
    finally:
        await mgr.cancel(task_id, "")


async def test_append_background_reminder_only_updates_history() -> None:
    mgr = BackgroundTaskManager()
    task_id = mgr.next_alias()
    messages = []
    mgr.launch(task_id, "run_subagent", {}, _slow_tool())

    try:
        appended = append_background_reminder(mgr, messages)
        assert appended is True
        assert len(messages) == 1
        assert messages[0].background_task_states[0].task_id == task_id
    finally:
        await mgr.cancel(task_id, "")
