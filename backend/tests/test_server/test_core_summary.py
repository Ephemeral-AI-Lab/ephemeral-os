"""Tests for chat route TaskCenter summary rendering helpers."""

from __future__ import annotations

from server.routers.core import _final_root_summary_text
from task_center import Status, Task, TaskSummary


def test_final_root_summary_text_uses_append_only_summaries() -> None:
    root = Task(id="t1", role="executor", input="prompt", status=Status.DONE)
    root.summaries.append(TaskSummary(kind="handoff", text="decompose", source_task_id="t1"))
    root.summaries.append(
        TaskSummary(kind="child_success", text="accepted by evaluator", source_task_id="ev")
    )

    assert _final_root_summary_text(root) == "accepted by evaluator"
