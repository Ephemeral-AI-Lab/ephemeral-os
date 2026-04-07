"""Helpers for dispatching background tool executions from the query loop.

This module owns every side-concern around launching, reminding, and
reporting background tasks so ``engine/core/query.py`` can stay focused
on the turn-by-turn conversation loop itself.

Two public surfaces:

* Pure functions — :func:`format_background_result`,
  :func:`wrap_command_with_pid_tracking`, :func:`make_kill_callback`,
  :func:`build_launched_message`. These stay free-standing because they
  are used in isolation and are trivial to unit test.

* :class:`BackgroundDispatcher` — a thin stateful wrapper that bundles
  the manager + sandbox + tool registry + executor callable for a single
  query turn. It deduplicates the previously-copy-pasted launch block
  and exposes a single :meth:`launch` entry point.
"""

from __future__ import annotations

import base64
import logging
import time as time_module
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from engine.runtime.background_tasks import (
    BackgroundTaskManager,
    KillCallback,
    TrackedBackgroundTask,
)
from message.messages import ConversationMessage, ToolResultBlock
from message.stream_events import (
    BackgroundTaskCompleted,
    BackgroundTaskStarted,
    StreamEvent,
    ToolExecutionCompleted,
)
from tools.core.base import ToolRegistry, ToolResult

if TYPE_CHECKING:
    from engine.core.query import QueryContext

logger = logging.getLogger(__name__)

MAX_BACKGROUND_OUTPUT: int = 2000

# Signature of the tool executor callable the dispatcher delegates to.
# Matches ``engine.core.query._execute_tool_call``.
ExecuteToolFn = Callable[..., Awaitable[ToolResultBlock]]


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def format_background_result(task: TrackedBackgroundTask) -> tuple[str, str]:
    """Return ``(output, status_label)`` for a finished background task."""
    output = task.result.output if task.result else "No output"
    if len(output) > MAX_BACKGROUND_OUTPUT:
        output = (
            f"[truncated, showing last {MAX_BACKGROUND_OUTPUT} chars]\n"
            f"...{output[-MAX_BACKGROUND_OUTPUT:]}"
        )
    status = "ERROR" if (task.result and task.result.is_error) else "COMPLETED"
    return output, status


def build_launched_message(alias: str, tool_name: str) -> str:
    """Return the stock ``[BACKGROUND LAUNCHED]`` tool-result payload."""
    return (
        f'[BACKGROUND LAUNCHED] task_id="{alias}" tool={tool_name}\n'
        f"REMEMBER this task_id — you must pass it to "
        f'wait_for_background_task(task_id="{alias}") or '
        f'cancel_background_task(task_id="{alias}"). '
        f"Completion will arrive as a [BACKGROUND {alias} COMPLETED] message."
    )


def build_completed_message(task: TrackedBackgroundTask) -> ConversationMessage:
    """Return the ``[BACKGROUND … COMPLETED]`` user message for a finished task."""
    output, status = format_background_result(task)
    return ConversationMessage.from_user_text(
        f"[BACKGROUND {task.task_id} {status}] "
        f"tool={task.tool_name} "
        f"note={task.task_note!r}\n\n{output}"
    )


def build_completed_event(task: TrackedBackgroundTask) -> BackgroundTaskCompleted:
    """Return the ``BackgroundTaskCompleted`` stream event for a finished task."""
    output, _ = format_background_result(task)
    return BackgroundTaskCompleted(
        task_id=task.task_id,
        tool_name=task.tool_name,
        output=output,
        is_error=task.result.is_error if task.result else False,
    )


def wrap_command_with_pid_tracking(command: str, task_id: str) -> str:
    """Wrap a shell command so it records its PID and runs under ``setsid``.

    The command is base64-encoded before interpolation so stray quotes in
    the user command cannot escape the outer ``sh -c '...'`` wrapper. The
    wrapper itself runs under ``setsid`` so the cancel path can signal the
    entire process group (wrapper + command + any children it spawned).
    """
    pid_file = f"/tmp/.eos_bg_{task_id}.pid"
    encoded = base64.b64encode(command.encode("utf-8")).decode("ascii")
    inner = (
        f"echo $$ > {pid_file}; "
        f'exec sh -c "$(echo {encoded} | base64 -d)"'
    )
    return f"setsid sh -c '{inner}' < /dev/null"


def make_kill_callback(sandbox: Any | None, task_id: str) -> KillCallback | None:
    """Build a kill callback that signals the PGID recorded in the PID file.

    Returns ``None`` when no sandbox is available (non-Daytona tools).
    """
    if sandbox is None:
        return None

    pid_file = f"/tmp/.eos_bg_{task_id}.pid"

    async def _kill() -> None:
        try:
            kill_script = (
                f"PID=$(cat {pid_file} 2>/dev/null); "
                f'if [ -n "$PID" ]; then '
                f"  kill -TERM -- -$PID 2>/dev/null; "
                f"  sleep 0.2; "
                f"  kill -KILL -- -$PID 2>/dev/null; "
                f"fi; "
                f"rm -f {pid_file}"
            )
            await sandbox.process.exec(kill_script, timeout=5)
        except Exception as exc:  # noqa: BLE001 — log & swallow, nothing else to do
            logger.warning(
                "Failed to kill background process for task %s: %s", task_id, exc
            )

    return _kill


async def inject_reminder(
    messages: list[ConversationMessage],
    manager: BackgroundTaskManager,
) -> list[ConversationMessage]:
    """Append a ``<system-reminder>`` block for every still-running task."""
    pending = [t for t in manager._tasks.values() if t.status == "running"]
    if not pending:
        return messages

    parts: list[str] = []
    for t in pending:
        elapsed = time_module.monotonic() - t.started_at
        label = t.task_note or t.tool_name
        header = (
            f'Background task_id="{t.task_id}" still running '
            f"({elapsed:.0f}s) — {label}"
        )
        new_lines, since = manager.get_reminder_diff(t.task_id)
        if new_lines:
            logs = "\n".join(new_lines)
            parts.append(
                f"<system-reminder>\n{header}\n"
                f"New output (last {len(new_lines)} lines):\n{logs}\n"
                f"</system-reminder>"
            )
        else:
            parts.append(
                f"<system-reminder>\n{header}\n"
                f"No new output in the last {since:.0f}s\n"
                f"</system-reminder>"
            )

    return list(messages) + [ConversationMessage.from_user_text("\n".join(parts))]


# ---------------------------------------------------------------------------
# BackgroundDispatcher
# ---------------------------------------------------------------------------


class BackgroundDispatcher:
    """Per-turn dispatcher that launches background tools via one code path.

    Replaces two previously-duplicated blocks in the query loop that
    differed only in their outer iteration condition.
    """

    def __init__(
        self,
        *,
        manager: BackgroundTaskManager,
        tool_registry: ToolRegistry,
        context: "QueryContext",
        execute_tool: ExecuteToolFn,
    ) -> None:
        self.manager = manager
        self.tool_registry = tool_registry
        self.context = context
        self.execute_tool = execute_tool

    # -- sandbox resolution ---------------------------------------------------

    @property
    def _sandbox(self) -> Any | None:
        return (self.context.tool_metadata or {}).get("daytona_sandbox")

    # -- single-tool launch ---------------------------------------------------

    async def launch(
        self,
        tool_call: Any,
    ) -> tuple[list[tuple[StreamEvent, None]], ToolResultBlock]:
        """Launch one background tool.

        Returns ``(events_to_yield, tool_result_block)``. The caller is
        responsible for yielding the events in order and appending the
        tool-result block to its results list.
        """
        task_note = str(tool_call.input.get("task_note", ""))
        clean_input = {
            k: v
            for k, v in tool_call.input.items()
            if k not in ("background", "task_note")
        }

        # Reject tools that don't opt-in to background execution.
        tool_def = self.tool_registry.get(tool_call.name)
        if tool_def is not None and not tool_def.supports_background:
            msg = f"Tool '{tool_call.name}' does not support background execution."
            events: list[tuple[StreamEvent, None]] = [
                (
                    ToolExecutionCompleted(
                        tool_name=tool_call.name, output=msg, is_error=True
                    ),
                    None,
                )
            ]
            return events, ToolResultBlock(
                tool_use_id=tool_call.id, content=msg, is_error=True
            )

        # daytona_bash gets physical-cancel plumbing.
        kill_callback: KillCallback | None = None
        if tool_call.name == "daytona_bash" and "command" in clean_input:
            clean_input = dict(clean_input)
            clean_input["command"] = wrap_command_with_pid_tracking(
                str(clean_input["command"]), tool_call.id
            )
            kill_callback = make_kill_callback(self._sandbox, tool_call.id)

        alias = self.manager.next_alias()
        coro = self._build_runner(tool_call.name, tool_call.id, clean_input, alias)

        launched_event: BackgroundTaskStarted = self.manager.launch(
            alias,
            tool_call.name,
            clean_input,
            coro,
            task_note=task_note,
            kill_callback=kill_callback,
        )

        return (
            [(launched_event, None)],
            ToolResultBlock(
                tool_use_id=tool_call.id,
                content=build_launched_message(alias, tool_call.name),
                is_error=False,
            ),
        )

    # -- runner coroutine -----------------------------------------------------

    def _build_runner(
        self,
        tool_name: str,
        tool_use_id: str,
        clean_input: dict[str, object],
        alias: str,
    ) -> Awaitable[ToolResult]:
        """Build the coroutine that the background task manager will await."""
        execute_tool = self.execute_tool
        context = self.context
        manager = self.manager

        async def _runner() -> ToolResult:
            block = await execute_tool(
                context,
                tool_name,
                tool_use_id,
                clean_input,
                extra_metadata={
                    "on_progress_line": manager.make_progress_callback(alias),
                    "background_task_id": alias,
                },
            )
            return ToolResult(output=block.content, is_error=block.is_error)

        return _runner()
