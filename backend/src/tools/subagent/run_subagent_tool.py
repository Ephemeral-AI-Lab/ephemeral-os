"""run_subagent — spawn a focused worker subagent as a background task.

The subagent is built and executed via the same `spawn_agent` /
`EphemeralAgent` machinery used for top-level agents. The only difference is
the loaded `AgentDefinition` carries `agent_type="subagent"`, which causes
the engine to:
  - skip registering the background-management toolkit (subagents cannot
    launch their own background tasks),
  - use the subagent's focused-worker system prompt.

The tool is `force_background=True`, so the engine ALWAYS dispatches it as
a background task. The parent peeks at live progress (last 5 messages) via
`check_background_progress` — that calls into the progress provider this
tool registers on the BackgroundTaskManager.
"""

from __future__ import annotations

import json
import logging
from typing import Any

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


# Number of trailing messages surfaced by the live-peek progress provider.
PEEK_MESSAGE_COUNT = 5
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
    """Render the last *n* messages of a subagent for the parent's peek view."""
    if not messages:
        return "(no messages yet)"
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
    supports_background=True,
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
    sandbox_id = context.metadata.get("sandbox_id") or None
    bg_manager = context.metadata.get("background_task_manager")
    task_id = context.metadata.get("background_task_id")

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

    try:
        agent = spawn_agent(
            parent_cfg,
            messages=[],
            agent_def=sub_def,
            latest_user_prompt=prompt,
            sandbox_id=sandbox_id if isinstance(sandbox_id, str) else None,
        )
    except Exception as exc:
        logger.exception("run_subagent: spawn_agent failed")
        return ToolResult(output=f"run_subagent: spawn failed: {exc}", is_error=True)

    # Register the live-peek progress provider — closes over the inner agent's
    # _messages list, so each peek returns a fresh snapshot of the last N
    # messages at the moment of the peek (not a stale historical buffer).
    if bg_manager is not None and isinstance(task_id, str):
        try:
            bg_manager.set_progress_provider(
                task_id,
                lambda: format_last_n_messages(agent._messages, PEEK_MESSAGE_COUNT),
            )
        except Exception:
            logger.debug("run_subagent: failed to register progress provider", exc_info=True)

    try:
        async for _event in agent.run(prompt):
            # Drain the event stream — agent.run drives _messages, which is
            # what the peek provider reads. We don't need per-event handling.
            pass
    except Exception as exc:
        logger.exception("run_subagent: subagent run crashed")
        return ToolResult(output=f"run_subagent: subagent crashed: {exc}", is_error=True)

    final_text = _extract_final_text(agent._messages)
    if not final_text:
        return ToolResult(output="(subagent produced no final text)", is_error=False)
    return ToolResult(output=final_text)


# Mark this tool as always-background. The engine (streaming_executor and the
# foreground fallback in query.py) honors this flag and dispatches the call
# via BackgroundTaskManager regardless of whether the LLM passed background=true.
run_subagent.force_background = True  # type: ignore[attr-defined]
