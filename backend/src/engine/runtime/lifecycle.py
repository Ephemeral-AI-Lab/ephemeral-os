"""Shared ephemeral-agent lifecycle.

Single entrypoint that spawns an agent, drives its run loop, persists its
audit row + token usage, and returns a structured result. Used by both the
top-level chat path (``execute_ephemeral_agent_run``) and the subagent
dispatch tool (``run_subagent``).

The terminal-tool contract is the result-delivery channel: when the agent's
loop exits via a successful ``is_terminal_tool=True`` call, that tool's
``ToolResult`` is exposed on :class:`EphemeralRunResult.terminal_result`. The
parent reads it directly — no envelope, no JSON wrapping, no message
re-extraction.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from agents.types import AgentDefinition
from message.messages import ConversationMessage, ToolResultBlock
from message.stream_events import StreamEvent, ThinkingDelta
from providers.types import UsageSnapshot
from tools.core.base import ExecutionMetadata, ToolResult

if TYPE_CHECKING:
    from compaction import SessionState
    from server.app_factory import SessionConfig

logger = logging.getLogger(__name__)

AgentStreamEmitter = Callable[[StreamEvent], Awaitable[None]]

EphemeralRunStatus = Literal["completed", "failed"]


@dataclass
class EphemeralRunResult:
    """Outcome of one :func:`run_ephemeral_agent` invocation."""

    run_id: str | None
    status: EphemeralRunStatus
    error: str | None
    terminal_result: ToolResult | None
    display_messages: list[ConversationMessage]
    api_messages_snapshot: list[ConversationMessage] | None
    usage: UsageSnapshot | None
    agent_name: str
    model: str
    reasoning: str | None
    event_count: int


def _last_terminal_tool_result(
    display_messages: list[ConversationMessage],
) -> ToolResult | None:
    """Walk *display_messages* backwards for the last terminating tool result.

    Identifies the result the engine stamped with ``does_terminate=True`` —
    set by ``execute_tool_with_hooks`` when a ``is_terminal_tool=True`` tool
    returned non-error. Returns the corresponding :class:`ToolResult` (with
    ``output``, ``metadata``, etc.) or ``None`` if the loop exited without a
    terminal call (e.g. nudge retries exhausted, resource limit, or a plain
    text response).
    """
    for msg in reversed(display_messages):
        if msg.role != "user":
            continue
        for block in reversed(msg.content):
            if isinstance(block, ToolResultBlock) and block.does_terminate:
                return ToolResult(
                    output=str(block.content),
                    is_error=block.is_error,
                    metadata=dict(block.metadata or {}),
                    does_terminate=True,
                    mode_transition=block.mode_transition,
                )
    return None


async def run_ephemeral_agent(
    config: "SessionConfig",
    prompt: str,
    *,
    agent_def: AgentDefinition | None = None,
    sandbox_id: str | None = None,
    initial_messages: list[ConversationMessage] | None = None,
    session_state: "SessionState | None" = None,
    persist_session: bool = True,
    parent_run_id: str | None = None,
    parent_task_id: str | None = None,
    on_event: AgentStreamEmitter | None = None,
    extra_tool_metadata: ExecutionMetadata | dict[str, Any] | None = None,
) -> EphemeralRunResult:
    """Spawn → track → run → persist usage → optionally save session history.

    Single source of truth for the ephemeral-agent lifecycle. Two known
    callers:

    - ``server.routers.core.execute_ephemeral_agent_run`` — top-level chat
      path. ``persist_session=True`` so the session store records the
      updated transcript.
    - ``tools.subagent.run_subagent_tool.run_subagent`` — subagent
      dispatch. ``persist_session=False`` (subagent runs are not part of
      the user-visible transcript) and ``parent_*_id`` set so the audit
      row carries lineage.

    Terminal-tool enforcement and the ``MAX_TERMINAL_NUDGE_RETRIES`` cycle
    live in ``run_query`` and apply identically to every caller.
    """
    from agents.run_tracker import AgentRunTracker
    from engine.runtime.agent import spawn_agent

    db_available = False
    if persist_session:
        try:
            from server.app_factory import agent_run_store as _ars
            db_available = _ars.is_ready
        except Exception:
            db_available = False

    messages: list[ConversationMessage]
    full_history: list[dict[str, Any]] | None = None
    if persist_session:
        from server.app_factory import session_store
        messages, loaded_state, full_history = session_store.load_session_state(config)
        if session_state is None:
            session_state = loaded_state
    else:
        messages = list(initial_messages or [])

    agent = spawn_agent(
        config,
        messages,
        agent_def=agent_def,
        session_state=session_state,
        sandbox_id=sandbox_id,
    )
    logger.info(
        "Spawned agent %r (model=%s, session=%s, parent_run=%s, parent_task=%s)",
        agent.agent_name,
        agent.model,
        getattr(config, "session_id", None),
        parent_run_id,
        parent_task_id,
    )

    if persist_session and db_available:
        from server.app_factory import session_store
        try:
            session_store.upsert(
                session_id=config.session_id,
                cwd=config.cwd,
                model=agent.model,
                message_count=0,
            )
        except Exception:
            logger.debug("Failed to ensure session record", exc_info=True)

    tracker = AgentRunTracker.create(
        session_id=getattr(config, "session_id", None),
        agent_name=agent.agent_name,
        input_query=prompt,
        parent_run_id=parent_run_id,
        parent_task_id=parent_task_id,
    )
    run_id = tracker.run_id

    if agent.query_context.tool_metadata is None:
        agent.query_context.tool_metadata = ExecutionMetadata()
    if extra_tool_metadata:
        agent.query_context.tool_metadata.update(extra_tool_metadata)
    if run_id is not None:
        agent.query_context.tool_metadata.agent_run_id = run_id

    event_count = 0
    run_error: str | None = None
    reasoning_parts: list[str] = []

    try:
        async for event in agent.run(prompt):
            event_count += 1
            if isinstance(event, ThinkingDelta):
                reasoning_parts.append(event.text)
            if on_event is not None:
                await on_event(event)
    except Exception as exc:
        run_error = str(exc)
        logger.exception("run_ephemeral_agent: agent run crashed")

    qc = agent.query_context
    api_snapshot = qc.api_messages_snapshot if qc is not None else None
    reasoning = "".join(reasoning_parts) if reasoning_parts else None

    response: list[dict[str, Any]] | None = None
    if persist_session:
        response = [
            m.model_dump(mode="json")
            for m in agent._display_messages[len(messages):]
        ]

    tracker.finish(
        status="failed" if run_error else "completed",
        display_messages=list(agent._display_messages),
        api_messages_snapshot=api_snapshot,
        response=response,
        reasoning=reasoning,
        error=run_error,
        event_count=event_count,
    )

    if db_available:
        from token_tracker.runtime import persist_run_usage
        from server.app_factory import usage_store
        persist_run_usage(
            usage_store=usage_store,
            session_id=config.session_id,
            run_id=run_id,
            agent_name=agent.agent_name,
            model_id=agent.model,
            usage=agent.total_usage,
        )

    if persist_session:
        _persist_session_history(
            config=config,
            agent=agent,
            input_message=prompt,
            initial_messages=messages,
            full_history=full_history or [],
            db_available=db_available,
        )

    terminal_result = (
        _last_terminal_tool_result(agent._display_messages)
        if not run_error
        else None
    )

    return EphemeralRunResult(
        run_id=run_id,
        status="failed" if run_error else "completed",
        error=run_error,
        terminal_result=terminal_result,
        display_messages=list(agent._display_messages),
        api_messages_snapshot=api_snapshot,
        usage=agent.total_usage,
        agent_name=agent.agent_name,
        model=agent.model,
        reasoning=reasoning,
        event_count=event_count,
    )


def _persist_session_history(
    *,
    config: "SessionConfig",
    agent: Any,
    input_message: str,
    initial_messages: list[ConversationMessage],
    full_history: list[dict[str, Any]],
    db_available: bool,
) -> None:
    """Update session store + uncompacted audit log after a run completes."""
    if not db_available:
        return
    from server.app_factory import session_store

    new_messages: list[dict[str, Any]] = []
    engine_msgs = agent._display_messages
    for i in range(len(engine_msgs) - 1, -1, -1):
        msg = engine_msgs[i]
        if msg.role == "user" and msg.text.strip() == input_message.strip():
            new_messages = [m.model_dump(mode="json") for m in engine_msgs[i:]]
            break
    if new_messages:
        full_history.extend(new_messages)

    try:
        session_store.upsert(
            session_id=config.session_id,
            cwd=config.cwd,
            model=agent.model,
            system_prompt=agent.query_context.system_prompt,
            messages=[m.model_dump(mode="json") for m in agent._display_messages],
            full_messages=full_history,
            usage=agent.total_usage.model_dump() if agent.total_usage else {},
            session_state=agent.query_context.session_state.to_dict()
            if agent.query_context.session_state
            else None,
            summary=input_message.strip()[:80],
            message_count=len(agent._display_messages),
        )
    except Exception:
        logger.warning("Failed to persist session history", exc_info=True)
