"""Shared prompt-formatting helpers for harness agent contexts."""

from __future__ import annotations

from task_center.model import TaskSummary


def render_summaries(summaries: list[TaskSummary]) -> str:
    """Render summary rows in the shared labeled-heading envelope format."""
    if not summaries:
        return "(none)"
    return "\n".join(
        f"- [{summary.kind}] {summary.source_task_id}: {summary.text}"
        for summary in summaries
    )
