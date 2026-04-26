"""Shared labels for rendering edit-history actors."""

from __future__ import annotations


def change_actor_label(change: object) -> str:
    """Return the most specific actor label available for a file change."""
    task_id = str(getattr(change, "task_id", "") or "").strip()
    if task_id:
        return task_id
    agent_run_id = str(getattr(change, "agent_run_id", "") or "").strip()
    if agent_run_id:
        return agent_run_id
    return "unknown-run"
