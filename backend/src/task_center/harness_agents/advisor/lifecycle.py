"""Advisor lifecycle operations for TaskCenter.

The advisor is a transient task that produces a single ``submit_advisor_feedback``
verdict. ``ask_advisor`` (the caller-side tool) creates the task, polls for
its terminal status, then reads the verdict off the task's summary.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from task_center.errors import TaskCenterError
from task_center.model import Status, Task, TaskId, TaskSummary

if TYPE_CHECKING:
    from task_center.runtime.task_center import TaskCenter


_ALLOWED_VERDICTS = frozenset({"accept", "reject"})


def encode_verdict(verdict: str, reason: str) -> str:
    """Wire format used to round-trip the verdict through summary.text."""
    if verdict not in _ALLOWED_VERDICTS:
        raise TaskCenterError(
            f"submit_advisor_feedback: verdict {verdict!r} not in {sorted(_ALLOWED_VERDICTS)!r}"
        )
    return f"{verdict}|{reason}"


def decode_verdict(text: str) -> tuple[str, str]:
    """Parse a verdict-encoded summary back into (verdict, reason)."""
    if "|" not in text:
        return ("reject", f"malformed advisor feedback: {text!r}")
    verdict, _, reason = text.partition("|")
    if verdict not in _ALLOWED_VERDICTS:
        return ("reject", f"unknown verdict {verdict!r} (reason: {reason})")
    return (verdict, reason)


def submit_advisor_feedback(
    tc: "TaskCenter", task_id: TaskId, verdict: str, reason: str
) -> None:
    """Record the advisor's verdict + reason on the advisor task and mark DONE."""
    task = tc.graph.get(task_id)
    if task.role != "advisor":
        raise TaskCenterError(
            f"submit_advisor_feedback: task {task_id!r} role {task.role!r} is not advisor"
        )
    encoded = encode_verdict(verdict, reason)
    task.summaries.append(
        TaskSummary(kind="advisor_feedback", text=encoded, source_task_id=task_id)
    )
    tc._mark_terminal(task, Status.DONE)
    tc._persist_all()
    tc._wakeup.set()


def handle_silent_termination(tc: "TaskCenter", task: Task, reason: str) -> None:
    """Treat a silent advisor exit as a reject verdict — fail-safe gating."""
    encoded = encode_verdict("reject", f"advisor crashed: {reason}")
    task.summaries.append(
        TaskSummary(kind="advisor_feedback", text=encoded, source_task_id=task.id)
    )
    tc._mark_terminal(task, Status.FAILED)
    tc._persist_all()
    tc._wakeup.set()
