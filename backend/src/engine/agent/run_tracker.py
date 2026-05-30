"""Centralised agent_run persistence.

:class:`AgentRunTracker` wraps the minimal ``agent_runs`` row for one
TaskCenter task. Direct eval-agent invocations pass ``task_id=None`` and are
not persisted.

Lifecycle:

    tracker = AgentRunTracker.create(
        task_id=..., agent_name=...,
    )
    ... run the agent, stream events ...
    tracker.finish(
        messages=...,
        terminal_tool_result=...,
        token_count=...,
        error=...,
    )

``agent_run_id`` is ALWAYS a freshly minted id, so callers always have a
stable agent-run identity to stamp onto events. Only the durable ``agent_runs``
row is gated on a task id being present and the store being ready (tracked by
``_persisted``); when not persisted, :meth:`finish` is a no-op.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from message.message import Message

logger = logging.getLogger(__name__)

_AUTO_RUN_ID_HEX_LEN = 16


def _get_agent_run_store() -> Any | None:
    """Return the live agent_run_store if it is importable and ready."""
    try:
        from runtime.app_factory import agent_run_store
    except Exception as exc:
        logger.debug("agent_run_store import failed: %s", exc)
        return None
    if not agent_run_store.is_ready:
        return None
    return agent_run_store


@dataclass
class AgentRunTracker:
    """Handle wrapping an ``agent_run``.

    ``agent_run_id`` is always a freshly minted id so callers always have a
    stable agent-run identity to stamp onto events. Only the durable
    ``agent_runs`` row is gated on ``_persisted`` (task id present + store
    ready); when not persisted, :meth:`finish` short-circuits to a no-op.
    """

    agent_run_id: str
    agent_name: str
    _persisted: bool = field(default=False, init=False)
    _finished: bool = field(default=False, init=False)

    @classmethod
    def create(
        cls,
        *,
        task_id: str | None,
        agent_name: str,
    ) -> AgentRunTracker:
        """Mint an agent-run id and, when possible, persist its ``agent_runs`` row.

        The returned tracker always carries a minted ``agent_run_id``. The
        durable row is written only when a task_id is present, the store is
        ready, and the insert succeeds (then ``_persisted`` is True); otherwise
        the id is an in-memory-only handle and :meth:`finish` is a no-op.
        """
        resolved_agent_run_id = uuid4().hex[:_AUTO_RUN_ID_HEX_LEN]
        if not task_id:
            return cls(agent_run_id=resolved_agent_run_id, agent_name=agent_name)

        store = _get_agent_run_store()
        if store is None:
            return cls(agent_run_id=resolved_agent_run_id, agent_name=agent_name)

        try:
            store.create_run(
                agent_run_id=resolved_agent_run_id,
                task_id=task_id,
                agent_name=agent_name,
            )
        except Exception:
            logger.warning(
                "AgentRunTracker.create: failed to persist agent_run row", exc_info=True
            )
            return cls(agent_run_id=resolved_agent_run_id, agent_name=agent_name)
        tracker = cls(agent_run_id=resolved_agent_run_id, agent_name=agent_name)
        tracker._persisted = True
        return tracker

    def finish(
        self,
        *,
        messages: list[Message] | None = None,
        terminal_tool_result: dict[str, Any] | None = None,
        token_count: int = 0,
        error: str | None = None,
    ) -> None:
        """Finalise the run row. No-op when persistence is unavailable."""
        if not self._persisted or self._finished:
            return
        store = _get_agent_run_store()
        if store is None:
            return
        try:
            message_history: list[dict[str, Any]] | None = None
            if messages is not None:
                message_history = [m.model_dump(mode="json") for m in messages]

            store.finish_run(
                self.agent_run_id,
                message_history=message_history,
                terminal_tool_result=terminal_tool_result,
                token_count=token_count,
                error=error,
            )
        except Exception:
            logger.warning(
                "AgentRunTracker.finish: failed to finalise agent_run row", exc_info=True
            )
        finally:
            self._finished = True
