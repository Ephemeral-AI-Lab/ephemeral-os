"""Subagent toolkit — parallel agent dispatch over work items.

Provides a single tool:
- ``run_parallel_agents``: fan out work items to worker agents with
  templated prompts, concurrency control, and interruption recovery.
"""

from __future__ import annotations

from typing import Any

from tools.base import BaseToolkit
from tools.subagent.parallel_dispatch_tool import RunParallelAgentsTool


class SubagentToolkit(BaseToolkit):
    """Parallel agent dispatch — fan out work items to worker agents."""

    def __init__(
        self,
        *,
        run_named_agent_fn: Any = None,
        goal: str = "",
        project_context: str = "",
        run_context: dict[str, object] | None = None,
        sandbox_id: str | None = None,
        coordination_store: Any = None,
        phase_outputs: dict[str, dict] | None = None,
    ) -> None:
        super().__init__(
            name="subagent",
            description="Parallel agent dispatch: fan out work items to worker agents",
            tools=[
                RunParallelAgentsTool(
                    run_named_agent_fn=run_named_agent_fn,
                    goal=goal,
                    project_context=project_context,
                    run_context=run_context,
                    sandbox_id=sandbox_id,
                    coordination_store=coordination_store,
                    phase_outputs=phase_outputs,
                ),
            ],
        )


__all__ = ["SubagentToolkit"]
