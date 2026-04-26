"""Summary helpers shared by graph closure and prompt context."""

from __future__ import annotations

from task_center.model import Task, TaskSummary


COMPLETED_CHILD_SUMMARY_KINDS = frozenset({"success", "child_success"})
FAILED_CHILD_SUMMARY_KINDS = frozenset({"failure", "child_failure"})
DEPENDENCY_BLOCKED_SUMMARY_KIND = "dependency_blocked"

DISPLAY_SUMMARY_KINDS = frozenset(
    {
        "success",
        "failure",
        "evaluation_failure",
        "dependency_blocked",
        "child_success",
        "child_failure",
    }
)


def child_summary_groups(
    task: Task,
) -> tuple[list[TaskSummary], list[TaskSummary], list[TaskSummary]]:
    """Split one executor's summaries into completed, failed, and blocked groups."""
    completed: list[TaskSummary] = []
    failed: list[TaskSummary] = []
    blocked: list[TaskSummary] = []
    for summary in task.summaries:
        if summary.kind in COMPLETED_CHILD_SUMMARY_KINDS:
            completed.append(summary)
        elif summary.kind in FAILED_CHILD_SUMMARY_KINDS:
            failed.append(summary)
        elif summary.kind == DEPENDENCY_BLOCKED_SUMMARY_KIND:
            blocked.append(summary)
    return completed, failed, blocked


def latest_summary_text(task: Task) -> str | None:
    """Return the newest non-empty terminal/display summary text for a task."""
    for summary in reversed(task.summaries):
        if summary.kind in DISPLAY_SUMMARY_KINDS and summary.text:
            return summary.text
    return None
