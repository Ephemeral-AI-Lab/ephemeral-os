"""Subagent toolkit — parallel agent dispatch over work items."""

from __future__ import annotations

from tools.base import BaseToolkit
from tools.subagent.parallel_dispatch_tool import AgentRunFn, make_run_parallel_agents_tool


class SubagentToolkit(BaseToolkit):
    """Parallel agent dispatch — fan out work items to worker agents."""

    def __init__(self, *, run_agent_fn: AgentRunFn | None = None) -> None:
        super().__init__(
            name="subagent",
            description="Parallel agent dispatch: fan out work items to worker agents",
            tools=[make_run_parallel_agents_tool(run_agent_fn=run_agent_fn)],
        )


__all__ = ["SubagentToolkit"]
