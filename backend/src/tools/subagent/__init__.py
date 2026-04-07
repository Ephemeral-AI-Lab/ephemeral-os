"""Subagent toolkit — spawn focused worker subagents."""

from __future__ import annotations

from tools.core.base import BaseToolkit
from tools.subagent.run_subagent_tool import run_subagent


class SubagentToolkit(BaseToolkit):
    """Spawn focused worker subagents that run as background tasks."""

    def __init__(self) -> None:
        super().__init__(
            name="subagent",
            description="Spawn focused worker subagents.",
            tools=[run_subagent],
            instructions=(
                "Use `run_subagent` to delegate a focused task to a worker.\n"
                "- Every call returns a task_id immediately — subagents ALWAYS run "
                "in the background.\n"
                "- For PARALLEL workers: emit several `run_subagent` calls in the "
                "SAME assistant turn. Each returns its own task_id.\n"
                "- Join with `wait_for_background_task(task_id=...)` to read the "
                "worker's final text.\n"
                "- Peek at a worker's live progress (last 5 messages) with "
                "`check_background_progress(task_id=...)`.\n"
                "- Cancel a stuck worker with `cancel_background_task(task_id=...)`.\n"
                "- Workers cannot themselves spawn subagents and cannot launch "
                "background tasks of their own."
            ),
        )


__all__ = ["SubagentToolkit"]
