"""Core tool-aware query loop."""

from __future__ import annotations

import asyncio
import base64
import logging
import re
import time as time_module
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from collections.abc import AsyncIterator

if TYPE_CHECKING:
    from compaction import SessionState

from providers.types import (
    ApiCancelEvent,
    ApiMessageCompleteEvent,
    ApiMessageRequest,
    ApiTextDeltaEvent,
    ApiThinkingDeltaEvent,
    ApiToolUseDeltaEvent,
    SupportsStreamingMessages,
    UsageSnapshot,
)
from message.messages import ConversationMessage, SystemReminderBlock, ToolResultBlock
from engine.runtime.background_tasks import BackgroundTaskManager, KillCallback, TrackedBackgroundTask
from message.stream_events import (
    AssistantTextDelta,
    AssistantTurnComplete,
    BackgroundTaskCompleted,
    StreamEvent,
    ThinkingDelta,
    ToolExecutionCancelled,
    ToolExecutionCompleted,
    ToolExecutionStarted,
)
from engine.core.streaming_executor import StreamingToolExecutor
from hooks import HookEvent, HookExecutor
from tools.core.base import (
    ToolExecutionContext,
    ToolRegistry,
    ToolResult,
    decorate_schemas_for_background,
)

logger = logging.getLogger(__name__)

MAX_OUTPUT_LENGTH: int = 2000
BACKGROUND_IDLE_TIMEOUT: int = 30  # Safety net — LLM should use wait_for_background_task explicitly
CANCEL_PATTERN = re.compile(r'\[CANCEL:(\S+)(?:\s+reason="([^"]*)")?\]')


@dataclass
class QueryContext:
    api_client: SupportsStreamingMessages
    tool_registry: ToolRegistry
    cwd: Path
    model: str
    system_prompt: str
    max_tokens: int
    max_turns: int = 200
    hook_executor: HookExecutor | None = None
    tool_metadata: dict[str, object] | None = None
    session_state: SessionState | None = None
    enable_background_tasks: bool = False
    # Snapshot of the most recent api_messages list sent to the provider.
    # Updated by the query loop on every turn. Persistence layers read this
    # to populate the ``compacted_history`` column without having to re-run
    # compaction. ``None`` until the first turn completes.
    api_messages_snapshot: list[ConversationMessage] | None = None


def _format_background_result(
    completed_task: TrackedBackgroundTask,
) -> tuple[str, str]:
    output = completed_task.result.output if completed_task.result else "No output"
    if len(output) > MAX_OUTPUT_LENGTH:
        output = (
            f"[truncated, showing last {MAX_OUTPUT_LENGTH} chars]\n...{output[-MAX_OUTPUT_LENGTH:]}"
        )
    status_label = (
        "ERROR" if (completed_task.result and completed_task.result.is_error) else "COMPLETED"
    )
    return output, status_label


def _build_background_reminder(
    background_manager: BackgroundTaskManager,
) -> ConversationMessage | None:
    """Build a single durable user message summarising live background tasks.

    Returns ``None`` if no tasks are running. The returned message is a
    regular ``ConversationMessage`` and is appended to *display_messages*
    so the user (and subsequent compaction passes) can see it. It is NOT
    a separate ephemeral concept — once appended, it lives in history.

    Calling this advances the per-task reminder cursor via
    :meth:`BackgroundTaskManager.get_reminder_diff`, so each call yields
    only progress lines that have appeared since the previous reminder.
    """
    pending = [t for t in background_manager._tasks.values() if t.status == "running"]
    if not pending:
        return None

    parts: list[str] = []
    for t in pending:
        elapsed = time_module.monotonic() - t.started_at
        label = t.task_note or t.tool_name
        header = (
            f"Background task_id=\"{t.task_id}\" still running "
            f"({elapsed:.0f}s) — {label}"
        )
        new_lines, since = background_manager.get_reminder_diff(t.task_id)
        if new_lines:
            logs = "\n".join(new_lines)
            parts.append(
                f"{header}\nNew output (last {len(new_lines)} lines):\n{logs}"
            )
        else:
            parts.append(f"{header}\nNo new output in the last {since:.0f}s")

    return ConversationMessage(
        role="user",
        content=[
            SystemReminderBlock(
                text="\n\n".join(parts),
                category="background_progress",
            )
        ],
    )


def _make_kill_callback(context: QueryContext, task_id: str) -> KillCallback | None:
    """Create a callback that kills the sandbox process for a background task.

    Sends a kill signal to the PID written by the wrapped command.  Returns
    None when no sandbox is available (non-Daytona tools).
    """
    sandbox = (context.tool_metadata or {}).get("daytona_sandbox")
    if sandbox is None:
        return None

    pid_file = f"/tmp/.eos_bg_{task_id}.pid"

    async def _kill() -> None:
        try:
            # PID file holds the session leader's PID, which equals the
            # process group ID (set via setsid). `kill -- -PGID` signals
            # every process in the group, killing the whole tree.
            # Guard empty PID explicitly so `kill -- -` isn't invoked with
            # an empty arg (harmless but noisy) when the file is missing
            # or the wrapper shell was killed before it wrote $$.
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
        except Exception as exc:
            # Kill failure can leave an orphaned process group — log loud
            # enough to be visible at default log level.
            logger.warning("Failed to kill background process for task %s: %s", task_id, exc)

    return _kill


def _wrap_command_with_pid_tracking(command: str, task_id: str) -> str:
    """Wrap a shell command to record its PID in a temp file.

    The command runs inside its own session/process group via `setsid` so
    that cancel can signal the entire tree (wrapper + command + any
    children it spawns). Without this, children of constructs like
    `cd dir && python run.py` get orphaned and keep mutating shared
    state after cancel.

    The user command is passed to the inner shell base64-encoded to
    avoid any quoting/escaping footguns: a stray single quote in
    `command` would otherwise terminate the `sh -c '...'` wrapper and
    allow unintended shell evaluation.
    """
    pid_file = f"/tmp/.eos_bg_{task_id}.pid"
    encoded = base64.b64encode(command.encode("utf-8")).decode("ascii")
    # Inside the setsid'd sh: record our own PID (== PGID) then exec the
    # decoded user command. `exec` replaces the shell so signals target
    # the user process directly; the PGID remains stable because exec
    # does not change it.
    # NOTE: `base64 -d` is GNU/BusyBox; the Daytona sandbox runs Linux,
    # so this is portable for our deployment. If the sandbox ever moves
    # to BSD/macOS the flag becomes `-D`.
    inner = (
        f'echo $$ > {pid_file}; '
        f'exec sh -c "$(echo {encoded} | base64 -d)"'
    )
    return f"setsid sh -c '{inner}' < /dev/null"


async def _run_query_loop(
    context: QueryContext,
    display_messages: list[ConversationMessage],
) -> AsyncIterator[tuple[StreamEvent, UsageSnapshot | None]]:
    """Run the agentic tool loop.

    Two distinct message lists are maintained:

    - ``display_messages``: the append-only full history. Owned by the
      caller (EphemeralAgent / EvalAgent), persisted, and shown to the
      user. The query loop only **appends** to this list — it never
      mutates existing entries and never removes anything. Background
      reminders, completion notifications, assistant turns, and tool
      results all land here.
    - ``api_messages``: the compacted view sent to the LLM provider.
      Rebuilt fresh at the start of every turn from ``display_messages``
      via :func:`compact_for_api`. Never persisted, never returned. The
      reminder is part of ``display_messages`` so it is automatically
      reflected in the next ``api_messages`` snapshot.
    """
    from compaction import SessionState, compact_for_api

    compact_state = context.session_state or SessionState()

    background_manager: BackgroundTaskManager | None = None
    if context.enable_background_tasks:
        background_manager = BackgroundTaskManager()
        if context.tool_metadata is None:
            context.tool_metadata = {}
        context.tool_metadata["background_task_manager"] = background_manager

    for _ in range(context.max_turns):
        if background_manager is not None:
            for completed_task in background_manager.collect_completed():
                output, status_label = _format_background_result(completed_task)
                display_messages.append(
                    ConversationMessage.from_user_text(
                        f"[BACKGROUND {completed_task.task_id} {status_label}] "
                        f"tool={completed_task.tool_name} "
                        f"note={completed_task.task_note!r}\n\n{output}"
                    )
                )
                yield (
                    BackgroundTaskCompleted(
                        task_id=completed_task.task_id,
                        tool_name=completed_task.tool_name,
                        output=output,
                        is_error=completed_task.result.is_error if completed_task.result else False,
                    ),
                    None,
                )

            # Append a fresh background reminder to the durable history so
            # the user sees it AND the next compaction pass picks it up.
            if background_manager.has_pending():
                reminder_msg = _build_background_reminder(background_manager)
                if reminder_msg is not None:
                    display_messages.append(reminder_msg)

        executor = StreamingToolExecutor(
            tool_registry=context.tool_registry,
            context=ToolExecutionContext(
                cwd=context.cwd,
                metadata=context.tool_metadata or {},
            ),
        )

        daytona_toolkit = context.tool_registry.get_toolkit("sandbox_operations")
        if daytona_toolkit is not None and getattr(daytona_toolkit, "sandbox_id", None):
            try:
                await daytona_toolkit.prepare_context_async(executor._context)
                if context.tool_metadata is None:
                    context.tool_metadata = {}
                context.tool_metadata.update(executor._context.metadata)
            except Exception as exc:
                logger.debug(
                    "Sandbox context injection skipped (sandbox may not be configured): %s",
                    exc,
                )

        final_message: ConversationMessage | None = None
        usage = UsageSnapshot()
        pending_cancel: dict[str, str] = {}

        # Build the api_messages view fresh from display_messages every turn.
        # compact_for_api never mutates display_messages — the only list that
        # ever reaches the provider is api_messages.
        api_messages = await compact_for_api(
            display_messages,
            api_client=context.api_client,
            model=context.model,
            system_prompt=context.system_prompt,
            state=compact_state,
        )
        # Persistence + introspection hook: callers can read this AFTER the
        # loop returns to capture the final compacted view sent to the LLM.
        context.api_messages_snapshot = api_messages

        async for event in context.api_client.stream_message(
            ApiMessageRequest(
                model=context.model,
                messages=api_messages,
                system_prompt=context.system_prompt,
                max_tokens=context.max_tokens,
                tools=decorate_schemas_for_background(
                    context.tool_registry,
                    context.tool_registry.to_api_schema(),
                ) if context.enable_background_tasks
                else context.tool_registry.to_api_schema(),
            )
        ):
            if isinstance(event, ApiThinkingDeltaEvent):
                logger.debug("STREAM: Received ApiThinkingDeltaEvent: text_len=%d", len(event.text))
                yield ThinkingDelta(text=event.text), None
                continue

            if isinstance(event, ApiTextDeltaEvent):
                if match := CANCEL_PATTERN.search(event.text):
                    tool_id, reason = match.groups()
                    pending_cancel[tool_id] = reason or "Cancelled by LLM"
                    logger.info(
                        "STREAM: Cancel pattern found in text: tool_id=%s reason=%s",
                        tool_id,
                        reason,
                    )
                logger.debug("STREAM: Received ApiTextDeltaEvent: text_len=%d", len(event.text))
                yield AssistantTextDelta(text=event.text), None
                continue

            if isinstance(event, ApiToolUseDeltaEvent):
                logger.info(
                    "STREAM: Received ApiToolUseDeltaEvent: id=%s name=%s input_keys=%s",
                    event.id,
                    event.name,
                    list(event.input.keys()) if event.input else None,
                )
                assistant_msg = final_message or ConversationMessage(role="assistant", content=[])
                started = executor.add_tool(event, assistant_msg)
                if started:
                    logger.info("STREAM: Yielding ToolExecutionStarted: name=%s", started.tool_name)
                    yield started, None
                for progress in executor.get_progress():
                    logger.debug(
                        "STREAM: Yielding ToolExecutionProgress: tool_id=%s", progress.tool_id
                    )
                    yield progress, None
                continue

            if isinstance(event, ApiCancelEvent):
                logger.info(
                    "STREAM: Received ApiCancelEvent: tool_id=%s reason=%s",
                    event.tool_id,
                    event.reason,
                )
                executor.cancel(event.tool_id, event.reason)
                continue

            if isinstance(event, ApiMessageCompleteEvent):
                logger.info(
                    "STREAM: Received ApiMessageCompleteEvent: tool_uses_count=%d",
                    len(event.message.tool_uses) if event.message.tool_uses else 0,
                )
                final_message = event.message
                usage = event.usage

        if final_message is None:
            raise RuntimeError(
                f"Model stream finished without a final message for model {context.model}. "
                "Check that the API endpoint, authentication, and model name are correct."
            )

        for tool_id, reason in pending_cancel.items():
            executor.cancel(tool_id, reason)

        for progress in executor.get_progress():
            yield progress, None

        display_messages.append(final_message)
        yield AssistantTurnComplete(message=final_message, usage=usage), usage

        if not final_message.tool_uses:
            if background_manager is None or not background_manager.has_pending():
                return

            completed_task = await background_manager.wait_any(timeout=BACKGROUND_IDLE_TIMEOUT)

            if completed_task is not None:
                output, status_label = _format_background_result(completed_task)
                display_messages.append(
                    ConversationMessage.from_user_text(
                        f"[BACKGROUND {completed_task.task_id} {status_label}] "
                        f"tool={completed_task.tool_name} "
                        f"note={completed_task.task_note!r}\n\n{output}"
                    )
                )
                yield (
                    BackgroundTaskCompleted(
                        task_id=completed_task.task_id,
                        tool_name=completed_task.tool_name,
                        output=output,
                        is_error=completed_task.result.is_error if completed_task.result else False,
                    ),
                    None,
                )
            else:
                display_messages.append(
                    ConversationMessage.from_user_text(background_manager.compact_status())
                )
            continue

        for started in executor.get_started_events():
            logger.info(
                "STREAM: Yielding (remaining) ToolExecutionStarted: name=%s", started.tool_name
            )
            yield started, None

        tool_results: list[ToolResultBlock] = []
        for completed in await executor.get_remaining():
            if isinstance(completed, ToolExecutionCompleted):
                logger.info(
                    "STREAM: Yielding ToolExecutionCompleted: name=%s is_error=%s output_len=%d",
                    completed.tool_name,
                    completed.is_error,
                    len(completed.output) if completed.output else 0,
                )
                tool_results.append(
                    ToolResultBlock(
                        tool_use_id=completed.tool_id,
                        content=completed.output,
                        is_error=completed.is_error,
                    )
                )
                yield completed, None
            elif isinstance(completed, ToolExecutionCancelled):
                logger.info(
                    "STREAM: Yielding ToolExecutionCancelled: name=%s reason=%s",
                    completed.tool_name,
                    completed.reason,
                )
                tool_results.append(
                    ToolResultBlock(
                        tool_use_id=completed.tool_id,
                        content=f"[CANCELLED] {completed.reason}",
                        is_error=True,
                    )
                )
                yield completed, None

        # --- Launch background tools that the streaming executor skipped ---
        skipped_bg = executor.skipped_background_ids
        if skipped_bg and background_manager is not None:
            for tc in final_message.tool_uses:
                if tc.id not in skipped_bg:
                    continue
                task_note = str(tc.input.get("task_note", ""))
                clean_input = {
                    k: v for k, v in tc.input.items() if k not in ("background", "task_note")
                }

                tool_def = context.tool_registry.get(tc.name)
                if tool_def and getattr(tool_def, "background", "forbidden") == "forbidden":
                    tool_results.append(
                        ToolResultBlock(
                            tool_use_id=tc.id,
                            content=f"Tool '{tc.name}' does not support background execution.",
                            is_error=True,
                        )
                    )
                    yield (
                        ToolExecutionCompleted(
                            tool_name=tc.name,
                            output=f"Tool '{tc.name}' does not support background execution.",
                            is_error=True,
                        ),
                        None,
                    )
                    continue

                # Wrap daytona_bash commands with PID tracking for physical cancel
                kill_callback = None
                if tc.name == "daytona_bash" and "command" in clean_input:
                    clean_input = dict(clean_input)
                    clean_input["command"] = _wrap_command_with_pid_tracking(
                        str(clean_input["command"]), tc.id
                    )
                    kill_callback = _make_kill_callback(context, tc.id)

                bg_alias = background_manager.next_alias()

                async def _bg_wrapper(
                    ctx: QueryContext,
                    name: str,
                    uid: str,
                    inp: dict[str, object],
                    alias: str = bg_alias,
                ) -> ToolResult:
                    block = await _execute_tool_call(
                        ctx, name, uid, inp,
                        extra_metadata={
                            "on_progress_line": background_manager.make_progress_callback(alias),
                            "background_task_id": alias,
                        },
                    )
                    return ToolResult(output=block.content, is_error=block.is_error)

                coro = _bg_wrapper(context, tc.name, tc.id, clean_input)
                bg_event = background_manager.launch(
                    bg_alias, tc.name, clean_input, coro, task_note=task_note,
                    kill_callback=kill_callback,
                )
                yield bg_event, None
                tool_results.append(
                    ToolResultBlock(
                        tool_use_id=tc.id,
                        content=(
                            f"[BACKGROUND LAUNCHED] task_id=\"{bg_alias}\" tool={tc.name}\n"
                            f"REMEMBER this task_id — you must pass it to "
                            f"wait_for_background_task(task_id=\"{bg_alias}\") or "
                            f"cancel_background_task(task_id=\"{bg_alias}\"). "
                            f"Completion will arrive as a [BACKGROUND {bg_alias} COMPLETED] message."
                        ),
                        is_error=False,
                    )
                )

        if not tool_results:
            executor.cancel_all()

            tool_calls = final_message.tool_uses
            foreground_calls = []

            for tc in tool_calls:
                task_note = str(tc.input.get("task_note", ""))
                tool_def_for_check = context.tool_registry.get(tc.name)
                force_bg = getattr(tool_def_for_check, "background", "forbidden") == "always"
                is_background = (
                    (tc.input.get("background", False) or force_bg)
                    if background_manager else False
                )
                clean_input = {
                    k: v for k, v in tc.input.items() if k not in ("background", "task_note")
                }

                if is_background:
                    tool_def = context.tool_registry.get(tc.name)
                    if tool_def and getattr(tool_def, "background", "forbidden") == "forbidden":
                        tool_results.append(
                            ToolResultBlock(
                                tool_use_id=tc.id,
                                content=f"Tool '{tc.name}' does not support background execution.",
                                is_error=True,
                            )
                        )
                        yield (
                            ToolExecutionCompleted(
                                tool_name=tc.name,
                                output=f"Tool '{tc.name}' does not support background execution.",
                                is_error=True,
                            ),
                            None,
                        )
                        continue

                    # Wrap daytona_bash commands with PID tracking for physical cancel
                    kill_callback = None
                    if tc.name == "daytona_bash" and "command" in clean_input:
                        clean_input = dict(clean_input)
                        clean_input["command"] = _wrap_command_with_pid_tracking(
                            str(clean_input["command"]), tc.id
                        )
                        kill_callback = _make_kill_callback(context, tc.id)

                    bg_alias = background_manager.next_alias()

                    async def _bg_wrapper(
                        ctx: QueryContext,
                        name: str,
                        uid: str,
                        inp: dict[str, object],
                        alias: str = bg_alias,
                    ) -> ToolResult:
                        block = await _execute_tool_call(
                            ctx, name, uid, inp,
                            extra_metadata={
                                "on_progress_line": background_manager.make_progress_callback(alias),
                                "background_task_id": alias,
                            },
                        )
                        return ToolResult(output=block.content, is_error=block.is_error)

                    coro = _bg_wrapper(context, tc.name, tc.id, clean_input)
                    event = background_manager.launch(
                        bg_alias, tc.name, clean_input, coro, task_note=task_note,
                        kill_callback=kill_callback,
                    )
                    yield event, None
                    tool_results.append(
                        ToolResultBlock(
                            tool_use_id=tc.id,
                            content=(
                                f"[BACKGROUND LAUNCHED] task_id=\"{bg_alias}\" tool={tc.name}\n"
                                f"REMEMBER this task_id — pass it to "
                                f"wait_for_background_task(task_id=\"{bg_alias}\") or "
                                f"cancel_background_task(task_id=\"{bg_alias}\"). "
                                f"Completion arrives as a [BACKGROUND {bg_alias} COMPLETED] message."
                            ),
                            is_error=False,
                        )
                    )
                else:
                    foreground_calls.append(tc)

            if len(foreground_calls) == 1:
                tc = foreground_calls[0]
                logger.info(
                    "STREAM: Executing single foreground tool: name=%s id=%s", tc.name, tc.id
                )
                yield (
                    ToolExecutionStarted(
                        tool_name=tc.name,
                        tool_input=tc.input,
                    ),
                    None,
                )
                result = await _execute_tool_call(context, tc.name, tc.id, tc.input)
                tool_results.append(result)
                yield (
                    ToolExecutionCompleted(
                        tool_name=tc.name,
                        output=result.content,
                        is_error=result.is_error,
                    ),
                    None,
                )
            elif foreground_calls:
                logger.info(
                    "STREAM: Executing PARALLEL foreground tools: count=%d names=%s",
                    len(foreground_calls),
                    [tc.name for tc in foreground_calls],
                )
                started_events = []
                for tc in foreground_calls:
                    started_events.append(
                        ToolExecutionStarted(
                            tool_name=tc.name,
                            tool_input=tc.input,
                        )
                    )
                    logger.info(
                        "STREAM: Yielding parallel ToolExecutionStarted: name=%s id=%s",
                        tc.name,
                        tc.id,
                    )
                    yield started_events[-1], None

                logger.debug(
                    "STREAM: Launching asyncio.gather for %d parallel tools", len(foreground_calls)
                )
                results = await asyncio.gather(
                    *[
                        _execute_tool_call(context, tc.name, tc.id, tc.input)
                        for tc in foreground_calls
                    ]
                )
                logger.info("STREAM: All parallel tools completed, gathering results")
                tool_results.extend(results)
                for tc, result in zip(foreground_calls, results, strict=True):
                    logger.info(
                        "STREAM: Yielding parallel ToolExecutionCompleted: name=%s is_error=%s output_len=%d",
                        tc.name,
                        result.is_error,
                        len(result.content) if result.content else 0,
                    )
                    yield (
                        ToolExecutionCompleted(
                            tool_name=tc.name,
                            output=result.content,
                            is_error=result.is_error,
                        ),
                        None,
                    )

        assigned_ids: set[str] = {tr.tool_use_id for tr in tool_results if tr.tool_use_id}
        unassigned_ids = [tu.id for tu in final_message.tool_uses if tu.id not in assigned_ids]
        for tr in tool_results:
            if not tr.tool_use_id and unassigned_ids:
                tr.tool_use_id = unassigned_ids.pop(0)

        display_messages.append(ConversationMessage(role="user", content=tool_results))  # type: ignore[arg-type]

    if background_manager is not None:
        await background_manager.cancel_all()

    yield (
        ToolExecutionCompleted(
            tool_name="",
            output=f"Agent stopped: maximum turn limit ({context.max_turns}) reached. "
            "The conversation was too long to complete in the allowed iterations.",
            is_error=True,
        ),
        None,
    )


async def run_query(
    context: QueryContext,
    display_messages: list[ConversationMessage],
) -> tuple[list[ConversationMessage], AsyncIterator[tuple[StreamEvent, UsageSnapshot | None]]]:
    """Run an agent loop against *display_messages*.

    The same list is returned so callers retain a reference to the
    append-only display history. The query loop appends to it in place;
    callers must not assume immutability.

    The compacted ``api_messages`` view is built fresh inside the loop and
    sent to the LLM provider — it is never returned. See
    :func:`compact_for_api`.
    """
    return display_messages, _run_query_loop(context, display_messages)


async def _execute_tool_call(
    context: QueryContext,
    tool_name: str,
    tool_use_id: str,
    tool_input: dict[str, object],
    extra_metadata: dict[str, object] | None = None,
) -> ToolResultBlock:
    if context.hook_executor is not None:
        pre_hooks = await context.hook_executor.execute(
            HookEvent.PRE_TOOL_USE,
            {
                "tool_name": tool_name,
                "tool_input": tool_input,
                "event": HookEvent.PRE_TOOL_USE.value,
            },
        )
        if pre_hooks.blocked:
            return ToolResultBlock(
                tool_use_id=tool_use_id,
                content=pre_hooks.reason or f"pre_tool_use hook blocked {tool_name}",
                is_error=True,
            )

    tool = context.tool_registry.get(tool_name)
    if tool is None:
        return ToolResultBlock(
            tool_use_id=tool_use_id,
            content=f"Unknown tool: {tool_name}",
            is_error=True,
        )

    try:
        parsed_input = tool.input_model.model_validate(tool_input)
    except Exception as exc:
        return ToolResultBlock(
            tool_use_id=tool_use_id,
            content=f"Invalid input for {tool_name}: {exc}",
            is_error=True,
        )

    try:
        result = await tool.execute(
            parsed_input,
            ToolExecutionContext(
                cwd=context.cwd,
                metadata={
                    "tool_registry": context.tool_registry,
                    **(context.tool_metadata or {}),
                    **(extra_metadata or {}),
                },
            ),
        )
    except Exception as exc:
        return ToolResultBlock(
            tool_use_id=tool_use_id,
            content=f"Tool execution failed: {exc}",
            is_error=True,
        )

    tool_result = ToolResultBlock(
        tool_use_id=tool_use_id,
        content=result.output,
        is_error=result.is_error,
    )
    if context.hook_executor is not None:
        await context.hook_executor.execute(
            HookEvent.POST_TOOL_USE,
            {
                "tool_name": tool_name,
                "tool_input": tool_input,
                "tool_output": tool_result.content,
                "tool_is_error": tool_result.is_error,
                "event": HookEvent.POST_TOOL_USE.value,
            },
        )
    return tool_result
