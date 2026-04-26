"""Centralised agent_run persistence.

Three call sites historically wrote the same create/finish pattern for
``agent_run_store``. :class:`AgentRunTracker` collapses that into one helper
instead of copy-pasting the same lifecycle code.

Lifecycle:

    tracker = AgentRunTracker.create(
        task_id=..., agent_name=...,
    )
    ... run the agent, stream events ...
    tracker.finish(
        status="completed",
        display_messages=...,
        final_text=...,
    )

When persistence is unavailable (store not ready, task_id missing, or an
exception during create), :attr:`run_id` is ``None`` and every
subsequent call on the tracker is a no-op.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from message.messages import ConversationMessage

logger = logging.getLogger(__name__)

_AUTO_RUN_ID_HEX_LEN = 16
_AUTO_RUN_ID_RETRIES = 5


def _get_agent_run_store() -> Any | None:
    """Return the live agent_run_store if it is importable and ready."""
    try:
        from server.app_factory import agent_run_store
    except Exception as exc:
        logger.debug("agent_run_store import failed: %s", exc)
        return None
    if not agent_run_store.is_ready:
        return None
    return agent_run_store


@dataclass
class AgentRunTracker:
    """Handle wrapping a persisted ``agent_run`` row.

    ``run_id`` is ``None`` when persistence is unavailable; all methods
    handle that case by short-circuiting to a no-op so call sites never
    need to branch on a None run id themselves.
    """

    run_id: str | None
    agent_name: str
    _finished: bool = field(default=False, init=False)

    @classmethod
    def create(
        cls,
        *,
        task_id: str | None,
        agent_name: str,
        input_query: str = "",
        run_id: str | None = None,
        parent_run_id: str | None = None,
        parent_task_id: str | None = None,
    ) -> AgentRunTracker:
        """Create a persisted run row and return a tracker wrapping it.

        Returns a no-op tracker (``run_id=None``) if the store is not
        ready, the task_id is missing, or the create call raises.
        """
        del input_query
        if not task_id:
            if parent_task_id or parent_run_id:
                logger.warning(
                    "AgentRunTracker.create: skipping persistence — task_id missing "
                    "(parent_task_id=%s, agent=%s)",
                    parent_task_id,
                    agent_name,
                )
            return cls(run_id=None, agent_name=agent_name)
        del parent_run_id, parent_task_id

        store = _get_agent_run_store()
        if store is None:
            return cls(run_id=None, agent_name=agent_name)

        retries = 1 if run_id else _AUTO_RUN_ID_RETRIES
        for attempt in range(retries):
            resolved_run_id = run_id or uuid4().hex[:_AUTO_RUN_ID_HEX_LEN]
            try:
                store.create_run(
                    run_id=resolved_run_id,
                    task_id=task_id,
                    agent_name=agent_name,
                )
            except Exception as exc:
                if run_id is None and _is_duplicate_key_error(exc) and attempt + 1 < retries:
                    logger.info(
                        "AgentRunTracker.create: duplicate run_id %s for %s, retrying",
                        resolved_run_id,
                        agent_name,
                    )
                    continue
                logger.warning(
                    "AgentRunTracker.create: failed to persist agent_run row", exc_info=True
                )
                return cls(run_id=None, agent_name=agent_name)
            return cls(run_id=resolved_run_id, agent_name=agent_name)
        return cls(run_id=None, agent_name=agent_name)

    def finish(
        self,
        *,
        status: str,
        display_messages: list[ConversationMessage] | None = None,
        response: Any | None = None,
        terminal_tool_result: dict[str, Any] | None = None,
        token_count: int = 0,
        reasoning: str | None = None,
        error: str | None = None,
        final_text: str = "",
        cancellation_reason: str | None = None,
        event_count: int | None = None,
    ) -> None:
        """Finalise the run row. No-op when persistence is unavailable."""
        del reasoning
        if self.run_id is None or self._finished:
            return
        store = _get_agent_run_store()
        if store is None:
            return
        try:
            message_history: list[dict[str, Any]] | None = None
            if display_messages is not None:
                message_history = [m.model_dump(mode="json") for m in display_messages]

            if response is None and final_text:
                response = {"final_text": final_text}

            resolved_event_count = (
                event_count
                if event_count is not None
                else (len(display_messages) if display_messages is not None else 0)
            )

            store.finish_run(
                self.run_id,
                status=status,
                message_history=message_history,
                terminal_tool_result=terminal_tool_result or response,
                token_count=token_count,
                error=error,
                event_count=resolved_event_count,
                cancellation_reason=cancellation_reason,
            )
        except Exception:
            logger.warning(
                "AgentRunTracker.finish: failed to finalise agent_run row", exc_info=True
            )
        finally:
            self._finished = True


def _is_duplicate_key_error(exc: Exception) -> bool:
    """Return True when *exc* is a duplicate-PK insertion failure."""
    text = str(exc).lower()
    return "duplicate key" in text or "unique constraint" in text
