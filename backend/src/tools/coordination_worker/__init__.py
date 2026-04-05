"""Coordination worker toolkit — replanning escalation for worker agents.

Provides a single tool:
- ``request_replan``: signal that a task encountered issues requiring
  replanning, persist context, and spawn a replanner task.
"""

from __future__ import annotations

from typing import Any, Callable

from tools.base import BaseToolkit
from tools.coordination_worker.replan_tool import RequestReplanTool


class CoordinationWorkerToolkit(BaseToolkit):
    """Worker escalation toolkit — request replanning when tasks hit issues."""

    def __init__(
        self,
        *,
        task_id: str = "",
        run_id: str = "",
        store: Any = None,
        plan: Any = None,
        agent_session_id: str | None = None,
        trigger_dispatch_fn: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(
            name="coordination_worker",
            description="Worker escalation: request replanning when tasks encounter issues",
            tools=[
                RequestReplanTool(
                    task_id=task_id,
                    run_id=run_id,
                    store=store,
                    plan=plan,
                    agent_session_id=agent_session_id,
                    trigger_dispatch_fn=trigger_dispatch_fn,
                ),
            ],
        )


__all__ = ["CoordinationWorkerToolkit"]
