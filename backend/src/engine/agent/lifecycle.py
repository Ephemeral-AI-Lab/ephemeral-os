"""Shared ephemeral-agent lifecycle.

Single entrypoint that spawns an agent, drives its run loop, persists its
audit row, and returns a structured result. Used by both the
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

from agents import AgentDefinition
from engine.query.context import QueryExitReason
from message.messages import ConversationMessage, TextBlock, ToolResultBlock
from message.stream_events import StreamEvent, ToolExecutionCompleted
from tools import ExecutionMetadata, ToolResult

if TYPE_CHECKING:
    from runtime.app_factory import RuntimeConfig

logger = logging.getLogger(__name__)

AgentStreamEmitter = Callable[[StreamEvent], Awaitable[None]]

EphemeralRunStatus = Literal["completed", "failed"]


@dataclass
class EphemeralRunResult:
    """Outcome of one :func:`run_ephemeral_agent` invocation."""

    status: EphemeralRunStatus
    error: str | None
    terminal_result: ToolResult | None
    agent_name: str
    event_count: int


def _build_retry_nudge(
    exit_reason: QueryExitReason,
    terminal_tools: set[str],
) -> str:
    """Compose the user-facing nudge appended before a retry attempt."""
    names = ", ".join(sorted(terminal_tools))
    if exit_reason == QueryExitReason.RESOURCE_LIMIT:
        return (
            f"Your tool-call budget was exhausted before you terminated. "
            f"You have been granted a fresh budget for one final attempt. "
            f"You MUST terminate immediately by calling one of: {names}. "
            f"Deliver whatever results you have so far via the terminal tool."
        )
    return (
        f"You replied with plain text without calling a terminal tool. "
        f"You MUST terminate now by calling one of: {names}. "
        f"Deliver your result via the terminal tool."
    )


def _prepare_retry_transcript(
    messages: list[ConversationMessage],
    exit_reason: QueryExitReason,
    nudge: str,
) -> None:
    """Inject the retry nudge into *messages* in place.

    On ``RESOURCE_LIMIT`` the transcript ends with a ``user`` message holding
    the tool_results from the cut-off batch; we merge the nudge into that
    message so role alternation stays idiomatic. On ``TEXT_RESPONSE`` the
    last message is the assistant's plain reply, so a fresh user message is
    appended.
    """
    if (
        exit_reason == QueryExitReason.RESOURCE_LIMIT
        and messages
        and messages[-1].role == "user"
    ):
        messages[-1] = ConversationMessage(
            role="user",
            content=[*messages[-1].content, TextBlock(text=nudge)],
        )
    else:
        messages.append(
            ConversationMessage(role="user", content=[TextBlock(text=nudge)])
        )


def _last_terminal_tool_result(
    messages: list[ConversationMessage],
) -> ToolResult | None:
    """Walk *messages* backwards for the last terminating tool result.

    Identifies the result the engine stamped with ``does_terminate=True`` when
    a ``is_terminal_tool=True`` tool returned non-error. Returns the
    corresponding :class:`ToolResult` (with
    ``output``, ``metadata``, etc.) or ``None`` if the loop exited without a
    terminal call (e.g. resource limit or a plain text response).
    """
    for msg in reversed(messages):
        if msg.role != "user":
            continue
        for block in reversed(msg.content):
            if isinstance(block, ToolResultBlock) and block.does_terminate:
                return ToolResult(
                    output=str(block.content),
                    is_error=block.is_error,
                    metadata=dict(block.metadata or {}),
                    does_terminate=True,
                )
    return None


async def run_ephemeral_agent(
    config: RuntimeConfig,
    prompt: str,
    *,
    agent_def: AgentDefinition | None = None,
    sandbox_id: str | None = None,
    initial_messages: list[ConversationMessage] | None = None,
    persist_agent_run: bool = True,
    task_id: str | None = None,
    on_event: AgentStreamEmitter | None = None,
    on_agent_spawned: Callable[[Any], None] | None = None,
    extra_tool_metadata: ExecutionMetadata | dict[str, Any] | None = None,
    max_terminal_retries: int = 1,
) -> EphemeralRunResult:
    """Spawn → track → run → persist a minimal agent run.

    Single source of truth for the ephemeral-agent lifecycle. TaskCenter
    callers pass ``task_id`` so the run can be attached to the corresponding
    ``task_center_tasks`` row. Subagent dispatches omit ``task_id`` and remain
    transient background work.

    Terminal tools end the run immediately on success. When the agent exits
    without delivering a terminal result — either by exhausting its tool-call
    budget (``RESOURCE_LIMIT``) or by replying in plain text
    (``TEXT_RESPONSE``) — the lifecycle injects a nudge prompt naming the
    available terminal tools, resets the tool-call budget and the
    budget-warning notification state, and re-enters the query loop, up to
    ``max_terminal_retries`` additional attempts. Crashes are never retried
    (callers that need recovery must spawn a fresh agent run with a new
    prompt). Pass ``max_terminal_retries=0`` to opt out and preserve the
    single-shot contract.
    """
    from engine.agent.run_tracker import AgentRunTracker
    from engine.agent.factory import spawn_agent

    db_available = False
    if persist_agent_run and task_id:
        try:
            from runtime.app_factory import agent_run_store as _ars
            db_available = _ars.is_ready
        except Exception:
            db_available = False

    messages = list(initial_messages or [])

    agent = spawn_agent(
        config,
        messages,
        agent_def=agent_def,
        sandbox_id=sandbox_id,
    )
    if on_agent_spawned is not None:
        try:
            on_agent_spawned(agent)
        except Exception:
            logger.debug("on_agent_spawned hook raised", exc_info=True)
    logger.info(
        "Spawned agent %r (model=%s, task_id=%s)",
        agent.agent_name,
        agent.model,
        task_id,
    )

    tracker = AgentRunTracker.create(
        task_id=task_id if db_available else None,
        agent_name=agent.agent_name,
    )
    agent_run_id = tracker.agent_run_id

    if agent.query_context.tool_metadata is None:
        agent.query_context.tool_metadata = ExecutionMetadata()
    if extra_tool_metadata:
        agent.query_context.tool_metadata.update(extra_tool_metadata)
    if task_id:
        agent.query_context.task_center_task_id = task_id
        agent.query_context.tool_metadata.task_center_task_id = task_id
    if agent_run_id is not None:
        agent.query_context.tool_metadata.agent_run_id = agent_run_id
    agent.query_context.run_id = task_id or agent_run_id or agent.query_context.run_id

    event_count = 0
    run_error: str | None = None
    terminal_result: ToolResult | None = None

    try:
        current_prompt: str | None = prompt
        max_attempts = max_terminal_retries + 1
        for attempt_idx in range(max_attempts):
            is_last_attempt = attempt_idx == max_attempts - 1
            try:
                async for event in agent.run(current_prompt, auto_close=False):
                    event_count += 1
                    if (
                        isinstance(event, ToolExecutionCompleted)
                        and event.does_terminate
                        and not event.is_error
                    ):
                        terminal_result = ToolResult(
                            output=event.output,
                            is_error=event.is_error,
                            metadata=dict(event.metadata or {}),
                            does_terminate=True,
                        )
                    if on_event is not None:
                        await on_event(event)
            except Exception as exc:
                run_error = str(exc)
                logger.exception("run_ephemeral_agent: agent run crashed")
                break  # crashes are never retried

            if terminal_result is not None or is_last_attempt:
                break

            exit_reason = getattr(agent.query_context, "exit_reason", None)
            terminal_tools: set[str] = getattr(
                agent.query_context, "terminal_tools", set()
            ) or set()
            if (
                exit_reason
                not in {
                    QueryExitReason.RESOURCE_LIMIT,
                    QueryExitReason.TEXT_RESPONSE,
                }
                or not terminal_tools
            ):
                break

            # Prepare retry: fresh budget, fresh exit reason, re-armed budget
            # warning rule, and a nudge appended to the live transcript.
            agent.query_context.tool_calls_used = 0
            agent.query_context.exit_reason = None
            notification_state = getattr(
                agent.query_context, "notification_state", None
            )
            if isinstance(notification_state, dict):
                notification_state.pop("budget_warning", None)
            nudge = _build_retry_nudge(exit_reason, terminal_tools)
            _prepare_retry_transcript(agent.messages, exit_reason, nudge)
            current_prompt = None
    finally:
        close = getattr(agent, "close", None)
        if close is not None:
            try:
                await close()
            except Exception:
                logger.debug("run_ephemeral_agent: agent.close raised", exc_info=True)
        if not run_error and terminal_result is None:
            terminal_result = agent.query_context.terminal_result or _last_terminal_tool_result(
                agent.messages
            )
        if run_error:
            terminal_result = None
        terminal_payload = (
            {
                "output": terminal_result.output,
                "is_error": terminal_result.is_error,
                "metadata": terminal_result.metadata,
                "does_terminate": terminal_result.does_terminate,
            }
            if terminal_result is not None
            else None
        )
        token_count = 0
        if agent.total_usage is not None:
            token_count = agent.total_usage.input_tokens + agent.total_usage.output_tokens

        tracker.finish(
            messages=list(agent.messages),
            terminal_tool_result=terminal_payload,
            token_count=token_count,
            error=run_error,
        )

    return EphemeralRunResult(
        status="failed" if run_error else "completed",
        error=run_error,
        terminal_result=terminal_result,
        agent_name=agent.agent_name,
        event_count=event_count,
    )
