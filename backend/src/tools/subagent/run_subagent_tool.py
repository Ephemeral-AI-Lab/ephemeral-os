"""run_subagent — spawn a focused worker subagent as a background task.

The subagent is built and executed via the same `spawn_agent` /
`EphemeralAgent` machinery used for top-level agents. The only difference is
the loaded `AgentDefinition` carries `agent_type="subagent"`, which causes
the engine to:
  - skip registering the background-management toolkit (subagents cannot
    launch their own background tasks),
  - use the subagent's focused-worker system prompt.

The tool is declared with ``background="always"``, so the engine ALWAYS
dispatches it as a background task regardless of LLM input. The parent
peeks at live progress (up to ``PEEK_MESSAGE_MAX`` trailing messages) via
``check_background_progress`` — that calls into the progress provider this
tool registers on the ``BackgroundTaskManager``.

The subagent's run is persisted to ``agent_run_store`` with ``parent_run_id``
+ ``parent_task_id`` set, so the parent can later list its workers, audit
their message history, and retry failed runs.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from uuid import uuid4

from message.messages import (
    ConversationMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from tools.core.base import ToolExecutionContext, ToolResult
from tools.core.decorator import tool

logger = logging.getLogger(__name__)


# Hard upper bound on the peek window — even if a caller (e.g. via the
# `last_n` parameter on check_background_progress) requests more, the
# subagent peek clamps to this so the parent's peek response stays bounded.
PEEK_MESSAGE_MAX = 10
# Per-block character cap inside the peek view.
_PEEK_BLOCK_CHAR_CAP = 200
# Total character cap for the peek view.
_PEEK_TOTAL_CHAR_CAP = 2048


def _truncate(s: str) -> str:
    s = s.replace("\n", " ").strip()
    if len(s) > _PEEK_BLOCK_CHAR_CAP:
        return s[: _PEEK_BLOCK_CHAR_CAP - 1] + "…"
    return s


def _compact_args(inp: Any) -> str:
    try:
        s = json.dumps(inp, separators=(",", ":"), default=str)
    except Exception:
        s = str(inp)
    return _truncate(s)


def _render_block(block: Any) -> str:
    """One-line render of a single content block."""
    if isinstance(block, TextBlock):
        return f"[text] {_truncate(block.text)}"
    if isinstance(block, ThinkingBlock):
        return f"[think] {_truncate(block.text)}"
    if isinstance(block, ToolUseBlock):
        return f"[tool] {block.name}({_compact_args(block.input)})"
    if isinstance(block, ToolResultBlock):
        return f"[result] {_truncate(str(block.content))}"
    return ""


def format_last_n_messages(messages: list[ConversationMessage], n: int) -> str:
    """Render the last *n* messages of a subagent for the parent's peek view.

    *n* is hard-clamped to ``PEEK_MESSAGE_MAX`` so a runaway caller cannot
    blow the parent's peek-response budget.
    """
    if not messages:
        return "(no messages yet)"
    n = min(n, PEEK_MESSAGE_MAX)
    tail = messages[-n:]
    rendered: list[str] = []
    for msg in tail:
        prefix = "U:" if msg.role == "user" else "A:"
        for block in msg.content:
            line = _render_block(block)
            if line:
                rendered.append(f"{prefix} {line}")
    if not rendered:
        return "(no renderable content yet)"
    out = "\n".join(rendered)
    if len(out) > _PEEK_TOTAL_CHAR_CAP:
        out = "…" + out[-(_PEEK_TOTAL_CHAR_CAP - 1) :]
    return out


def _extract_final_text(messages: list[ConversationMessage]) -> str:
    """Pull the assistant text out of the subagent's last assistant message."""
    for msg in reversed(messages):
        if msg.role != "assistant":
            continue
        text = msg.text
        if text:
            return text.strip()
    return ""


@tool(
    name="run_subagent",
    description=(
        "Spawn a focused worker subagent to complete a delegated task. "
        "ALWAYS runs as a background task — returns a task_id immediately. "
        "Join with wait_for_background_task(task_id=...). Peek at live progress "
        "(last 5 messages) with check_background_progress(task_id=...). Cancel "
        "with cancel_background_task(task_id=...). To run several subagents "
        "in parallel, emit multiple run_subagent calls in the same turn."
    ),
    background="always",
)
async def run_subagent(
    prompt: str,
    *,
    context: ToolExecutionContext,
) -> ToolResult:
    """Spawn a focused worker subagent.

    Args:
        prompt: The task description for the subagent.

    Returns:
        output (str): The subagent's final assistant text.
    """
    from agents import get_definition
    from engine.runtime.agent import spawn_agent

    parent_cfg = context.metadata.get("session_config")
    raw_sandbox_id = context.metadata.get("sandbox_id")
    sandbox_id = raw_sandbox_id if isinstance(raw_sandbox_id, str) and raw_sandbox_id else None
    bg_manager = context.metadata.get("background_task_manager")
    task_id = context.metadata.get("background_task_id")
    parent_run_id = context.metadata.get("agent_run_id")
    parent_task_id = task_id if isinstance(task_id, str) else None

    if parent_cfg is None:
        return ToolResult(
            output="run_subagent: missing session_config in execution context",
            is_error=True,
        )

    sub_def = get_definition("subagent")
    if sub_def is None:
        return ToolResult(
            output="run_subagent: builtin 'subagent' agent definition not found.",
            is_error=True,
        )

    # Persist a subagent run record FIRST, before spawn_agent — so spawn
    # failures still leave an audit trail that the parent can list / inspect
    # / retry. Reuses the parent's session_id (FK requirement) but sets
    # parent_run_id so the default `list_runs(session_id)` query filters
    # this row out of the user-facing transcript.
    sub_run_id = _create_subagent_run_record(
        parent_run_id=parent_run_id if isinstance(parent_run_id, str) else None,
        parent_task_id=parent_task_id,
        session_id=getattr(parent_cfg, "session_id", None),
        agent_name=sub_def.name,
        prompt=prompt,
    )

    try:
        agent = spawn_agent(
            parent_cfg,
            messages=[],
            agent_def=sub_def,
            latest_user_prompt=prompt,
            sandbox_id=sandbox_id,
        )
    except Exception as exc:
        logger.exception("run_subagent: spawn_agent failed")
        # Mark the run as failed at the spawn stage. No messages to capture
        # because the agent never started.
        _finish_subagent_run_record(
            sub_run_id,
            status="failed",
            display_messages=[],
            api_messages_snapshot=None,
            error=f"spawn_agent failed: {exc}",
            final_text="",
        )
        return ToolResult(output=f"run_subagent: spawn failed: {exc}", is_error=True)

    # Register the live-peek progress provider — closes over the inner agent's
    # _messages list, so each peek returns a fresh snapshot of the last N
    # messages at the moment of the peek (not a stale historical buffer).
    # Also back-link sub_run_id and tag task_type so the in-memory bg task
    # and the persisted audit row can be cross-resolved by either id.
    if bg_manager is not None and isinstance(task_id, str):
        # The bg manager calls the provider with the user-supplied `last_n`
        # from check_background_progress. format_last_n_messages clamps it
        # to PEEK_MESSAGE_MAX so the response stays bounded.
        bg_manager.set_progress_provider(
            task_id,
            lambda last_n: format_last_n_messages(agent.display_messages, last_n),
        )
        tracked = bg_manager.get_task(task_id) if hasattr(bg_manager, "get_task") else None
        if tracked is None:
            logger.warning(
                "run_subagent: bg_manager.get_task(%s) returned None — "
                "tracked.run_id will stay None",
                task_id,
            )
        else:
            tracked.run_id = sub_run_id
            tracked.task_type = "subagent"
            if sub_run_id is None:
                logger.warning(
                    "run_subagent: sub_run_id is None for task %s — "
                    "BackgroundTaskCompleted.run_id will be null",
                    task_id,
                )

    run_error: str | None = None
    cancelled = False
    try:
        async for _event in agent.run(prompt):
            # Drain the event stream — agent.run drives _messages, which is
            # what the peek provider reads. We don't need per-event handling.
            pass
    except asyncio.CancelledError:
        cancelled = True
        logger.info("run_subagent: subagent cancelled via bg manager")
    except Exception as exc:
        run_error = str(exc)
        logger.exception("run_subagent: subagent run crashed")

    # If cancel() was called on this bg task, prefer cancellation framing over
    # a generic failure — even if the agent.run loop happened to exit normally
    # before the cancel propagated.
    cancel_reason: str | None = None
    if bg_manager is not None and isinstance(task_id, str):
        tracked = bg_manager.get_task(task_id) if hasattr(bg_manager, "get_task") else None
        if tracked is not None and tracked.status == "cancelled":
            cancelled = True
            cancel_reason = tracked.cancel_reason

    final_text = _extract_final_text(agent.display_messages)
    # Tolerate test stubs that don't expose a query_context.
    qc = getattr(agent, "query_context", None)
    api_snapshot = qc.api_messages_snapshot if qc is not None else None
    if cancelled:
        final_status = "cancelled"
    elif run_error:
        final_status = "failed"
    else:
        final_status = "completed"
    _finish_subagent_run_record(
        sub_run_id,
        status=final_status,
        display_messages=agent.display_messages,
        api_messages_snapshot=api_snapshot,
        error=run_error,
        final_text=final_text,
        cancellation_reason=cancel_reason,
    )

    if cancelled:
        msg = f"run_subagent: cancelled ({cancel_reason})" if cancel_reason else "run_subagent: cancelled"
        # Re-raise so the bg manager's done callback observes a cancelled task
        # and the parent's wait/peek paths see consistent cancelled framing.
        raise asyncio.CancelledError(msg)
    if run_error:
        return ToolResult(output=f"run_subagent: subagent crashed: {run_error}", is_error=True)
    if not final_text:
        return ToolResult(output="(subagent produced no final text)", is_error=False)
    return ToolResult(output=final_text)


def _create_subagent_run_record(
    *,
    parent_run_id: str | None,
    parent_task_id: str | None,
    session_id: str | None,
    agent_name: str,
    prompt: str,
) -> str | None:
    """Create the subagent's agent_run row. Returns the new run_id, or None
    if persistence is unavailable (DB not initialised, or session_id missing).

    Import errors are intentionally NOT swallowed — if ``server.app_factory``
    cannot be imported, something is structurally broken and hiding it would
    just delay the diagnosis.
    """
    if not session_id:
        logger.warning(
            "run_subagent: skipping persistence — session_id missing "
            "(parent_task_id=%s, agent=%s)",
            parent_task_id,
            agent_name,
        )
        return None
    from server.app_factory import agent_run_store

    if not agent_run_store.is_ready:
        logger.warning(
            "run_subagent: skipping persistence — agent_run_store not ready "
            "(parent_task_id=%s, session_id=%s)",
            parent_task_id,
            session_id,
        )
        return None
    run_id = uuid4().hex[:12]
    if len(prompt) > 2000:
        logger.info(
            "run_subagent: input_query truncated from %d to 2000 chars for persistence",
            len(prompt),
        )
    try:
        agent_run_store.create_run(
            run_id=run_id,
            session_id=session_id,
            agent_name=agent_name,
            input_query=prompt[:2000],
            parent_run_id=parent_run_id,
            parent_task_id=parent_task_id,
        )
    except Exception:
        logger.warning(
            "run_subagent: failed to persist subagent run record", exc_info=True
        )
        return None
    return run_id


def _finish_subagent_run_record(
    run_id: str | None,
    *,
    status: str,
    display_messages: list[ConversationMessage],
    api_messages_snapshot: list[ConversationMessage] | None = None,
    error: str | None,
    final_text: str,
    cancellation_reason: str | None = None,
) -> None:
    """Finalise the subagent's agent_run row.

    Stores the full append-only display history in ``message_history`` and
    the final compacted view sent to the LLM in ``compacted_history``. The
    latter may be ``None`` if the subagent crashed before any turn ran.
    """
    if run_id is None:
        return
    from server.app_factory import agent_run_store

    if not agent_run_store.is_ready:
        return
    try:
        full_display = [m.model_dump(mode="json") for m in display_messages]
        compacted = (
            [m.model_dump(mode="json") for m in api_messages_snapshot]
            if api_messages_snapshot is not None
            else None
        )
        agent_run_store.finish_run(
            run_id,
            status=status,
            response={"final_text": final_text} if final_text else None,
            message_history=full_display,
            compacted_history=compacted,
            error=error,
            event_count=len(display_messages),
            cancellation_reason=cancellation_reason,
        )
    except Exception:
        logger.warning(
            "run_subagent: failed to finalise subagent run record", exc_info=True
        )
