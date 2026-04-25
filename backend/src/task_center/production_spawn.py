"""Production ``SpawnFunc`` that drives a real EphemeralAgent per task.

The dispatcher in :mod:`task_center.center` calls ``spawn_func(task_id, tc)``
for each ``READY`` task. In production this needs to:

1. Look up the agent definition by ``task.role`` (executor / evaluator).
2. Spawn a fresh ``EphemeralAgent`` via ``engine.runtime.agent.spawn_agent``.
3. Inject ``task_center``, ``task_id``, ``role`` into the agent's tool
   metadata so the submission tools can call back into TaskCenter.
4. Drive ``agent.run(spec)`` to completion, forwarding events to the
   TaskCenter-owned event callback (which the chat router connects to its
   SSE stream).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from task_center.center import TaskCenter

logger = logging.getLogger(__name__)


def make_production_spawn(session_config: Any, sandbox_id: str | None = None):
    """Build a ``SpawnFunc`` bound to a session config and optional sandbox."""

    async def spawn(task_id: str, tc: "TaskCenter") -> None:
        from agents.registry import get_definition
        from engine.runtime.agent import spawn_agent
        from task_center.errors import TaskCenterError
        from tools.core.base import ExecutionMetadata

        task = tc.graph.get(task_id)
        agent_def = get_definition(task.role)
        if agent_def is None:
            raise TaskCenterError(
                f"production spawn: no agent definition registered for role "
                f"{task.role!r} (expected 'executor' or 'evaluator')"
            )

        agent = spawn_agent(
            session_config,
            messages=[],
            agent_def=agent_def,
            sandbox_id=sandbox_id,
            terminal_tools=set(agent_def.terminal_tools),
        )

        # Inject TaskCenter handle so submission tools can call back.
        if agent.query_context.tool_metadata is None:
            agent.query_context.tool_metadata = ExecutionMetadata()
        meta = agent.query_context.tool_metadata
        meta["task_center"] = tc
        meta["task_id"] = task_id
        meta["role"] = task.role

        # Drive the agent loop. Forward each event to TaskCenter's callback.
        try:
            async for event in agent.run(task.spec):
                await tc._emit_event(event)
        except Exception:
            logger.exception("production spawn: agent for %r crashed", task_id)
            raise

    return spawn
