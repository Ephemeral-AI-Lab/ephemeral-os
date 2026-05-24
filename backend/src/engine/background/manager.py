"""Background task manager for async tool execution."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from collections.abc import Callable, Coroutine, Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from engine.background.subagent_policy import (
    DEFAULT_BACKGROUND_TASK_TYPE,
    mark_completion_mode_if_stopped,
    request_subagent_early_stop,
    should_cancel_asyncio_task,
)
from tools import ToolResult
from message.stream_events import BackgroundTaskStarted

logger = logging.getLogger(__name__)
_HEARTBEAT_INTERVAL_S = float(os.environ.get("EOS_BACKGROUND_HEARTBEAT_INTERVAL_S", "60"))


# Terminal status precedence used by :meth:`BackgroundTaskManager._set_terminal_status`.
# A status with a *higher* precedence overwrites a lower one; otherwise the
# attempt is dropped. This is the single-terminal-status latch the plan
# requires (Pre-mortem #6): cancel + natural-completion races resolve to
# COMPLETED so a long-running shell that finishes between cancel and reap
# returns its real result, not the "cancelled" overlay.
_TERMINAL_PRECEDENCE: dict[str, int] = {
    "running": 0,
    "cancelled": 1,
    "failed": 2,
    "completed": 3,
    "delivered": 4,
}


class TaskStatus(StrEnum):
    """Lifecycle states for a tracked background task.

    Transitions:
        RUNNING -> {COMPLETED, FAILED, CANCELLED} -> DELIVERED

    Only :meth:`BackgroundTaskManager.collect_completed` advances a task
    from a terminal state (COMPLETED/FAILED/CANCELLED) to DELIVERED.
    """

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    DELIVERED = "delivered"


# Terminal states that are still "undelivered" and waiting for the engine
# to pick them up via :meth:`BackgroundTaskManager.collect_completed`.
_TERMINAL_UNDELIVERED: frozenset[TaskStatus] = frozenset(
    {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
)


@dataclass
class TrackedBackgroundTask:
    """A background task tracked by the manager."""

    task_id: str
    tool_name: str
    tool_input: dict[str, Any]
    asyncio_task: asyncio.Task[ToolResult]
    # Discriminator so monitoring/UI/audit can branch without sniffing tool_name.
    # "agent" for ordinary background tools, "subagent" for run_subagent.
    task_type: str = DEFAULT_BACKGROUND_TASK_TYPE
    # Optional back-reference to a persisted AgentRunRecord (set by run_subagent
    # so the audit row and the in-memory bg task can be cross-resolved).
    agent_run_id: str | None = None
    agent_id: str | None = None
    uses_sandbox: bool = False
    sandbox_id: str | None = None
    sandbox_invocation_id: str | None = None
    status: TaskStatus = TaskStatus.RUNNING
    # Reason captured by cancel(); kept on the tracked task so callers (and
    # the subagent finaliser) can persist it to the audit record.
    cancel_reason: str | None = None
    # Cancellation / stop mode requested by the manager. Ordinary tools use
    # "cancel"; subagents may use "early_stop" so the task can salvage a
    # partial result before reaching a terminal state.
    stop_mode: str | None = None
    # Final completion flavor for successful-but-interrupted tasks.
    completion_mode: str | None = None
    result: ToolResult | None = None
    started_at: float = field(default_factory=time.monotonic)
    progress_lines: list[str] = field(default_factory=list)
    # Optional pull-callback that returns a fresh progress snapshot on demand.
    # Used by tools (e.g. run_subagent) that have structured progress state
    # which is more meaningful than a flat line buffer.
    progress_provider: Callable[[int], str] | None = None
    # Single-writer latch around the status/result mutation. The cancel path
    # and the asyncio done-callback can both race to set a terminal status;
    # the lock + ``_TERMINAL_PRECEDENCE`` table make that race deterministic.
    _terminal_lock: threading.Lock = field(default_factory=threading.Lock)


class BackgroundTaskManager:
    """Manages async background tasks launched by the query loop.

    This is dumb plumbing -- no error detection, no auto-cancel, no alerts.
    The LLM is the decision-maker.
    """

    def __init__(self) -> None:
        self._tasks: dict[str, TrackedBackgroundTask] = {}
        self._alias_counter: int = 0
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._engine_process_id = str(os.getpid())
        self._engine_started_at = time.time()

    def next_alias(self) -> str:
        """Return a short mnemonic task_id like 'bg_1', 'bg_2', ...

        These are easier for the LLM to retain in tool outputs than opaque
        tool_use_ids and are what the agent sees as ``task_id`` everywhere.
        """
        self._alias_counter += 1
        return f"bg_{self._alias_counter}"

    def launch(
        self,
        task_id: str,
        tool_name: str,
        tool_input: dict[str, Any],
        coro: Coroutine[Any, Any, ToolResult],
        task_type: str = DEFAULT_BACKGROUND_TASK_TYPE,
        agent_run_id: str | None = None,
        agent_id: str | None = None,
        uses_sandbox: bool = False,
        sandbox_id: str | None = None,
        sandbox_invocation_id: str | None = None,
    ) -> BackgroundTaskStarted:
        """Launch *coro* as a background task and return a started event."""
        asyncio_task = asyncio.create_task(coro)
        tracked = TrackedBackgroundTask(
            task_id=task_id,
            tool_name=tool_name,
            tool_input=tool_input,
            asyncio_task=asyncio_task,
            task_type=task_type,
            agent_run_id=agent_run_id,
            agent_id=agent_id,
            uses_sandbox=uses_sandbox,
            sandbox_id=sandbox_id,
            sandbox_invocation_id=sandbox_invocation_id,
        )
        start_line = f"[started: {tool_name}]"
        tracked.progress_lines.append(start_line)
        self._tasks[task_id] = tracked

        def _done_callback(task: asyncio.Task[ToolResult]) -> None:
            try:
                if task.cancelled():
                    self._set_terminal_status(
                        tracked,
                        new_status=TaskStatus.CANCELLED,
                        new_result=ToolResult(output="Cancelled", is_error=True),
                    )
                elif task.exception() is not None:
                    exc = task.exception()
                    self._set_terminal_status(
                        tracked,
                        new_status=TaskStatus.FAILED,
                        new_result=ToolResult(output=str(exc), is_error=True),
                    )
                else:
                    real_result = task.result()
                    applied = self._set_terminal_status(
                        tracked,
                        new_status=TaskStatus.COMPLETED,
                        new_result=real_result,
                    )
                    if applied:
                        mark_completion_mode_if_stopped(tracked)
            except Exception as exc:
                logger.debug("done_callback failed for %s: %s", tracked.task_id, exc)
                self._set_terminal_status(
                    tracked,
                    new_status=TaskStatus.FAILED,
                    new_result=ToolResult(
                        output="Unknown error in done callback",
                        is_error=True,
                    ),
                )

            # Populate progress_lines from whichever result the latch settled on.
            if tracked.result is not None and tracked.result.output:
                tracked.progress_lines = tracked.result.output.splitlines()
            self._stop_heartbeat_if_idle()

        asyncio_task.add_done_callback(_done_callback)
        if tracked.uses_sandbox and tracked.sandbox_invocation_id and tracked.sandbox_id:
            self._ensure_heartbeat_task()

        return BackgroundTaskStarted(
            task_id=task_id,
            tool_name=tool_name,
            tool_input=tool_input,
        )

    def collect_completed(self) -> list[TrackedBackgroundTask]:
        """Return tasks that finished but haven't been delivered yet.

        Each returned task is marked as ``delivered`` so it won't be
        returned again. This is the *only* method that performs the
        terminal → delivered transition.
        """
        ready: list[TrackedBackgroundTask] = []
        for tracked in self._tasks.values():
            if tracked.status in _TERMINAL_UNDELIVERED:
                tracked.status = TaskStatus.DELIVERED
                ready.append(tracked)
        return ready

    def iter_all(self) -> Iterator[TrackedBackgroundTask]:
        """Iterate every task the manager has ever tracked."""
        return iter(self._tasks.values())

    def iter_running(self) -> Iterator[TrackedBackgroundTask]:
        """Iterate tasks that are still running."""
        return (t for t in self._tasks.values() if t.status == TaskStatus.RUNNING)

    def has_pending(self) -> bool:
        """Return True if any task is still running."""
        return any(t.status == TaskStatus.RUNNING for t in self._tasks.values())

    def count_by_agent(self, agent_id: str) -> int:
        """Return running sandbox-bound background task count for one agent."""
        return sum(
            1
            for tracked in self._tasks.values()
            if tracked.status == TaskStatus.RUNNING
            and tracked.uses_sandbox
            and tracked.agent_id == agent_id
        )

    def append_progress(self, task_id: str, line: str) -> None:
        """Append a live progress line for *task_id*.

        Used by streaming-capable tools to push incremental output into the
        manager so that ``check_background_task_result`` can return a live tail
        while the task is still running. Splits *line* on newlines so the
        caller can pass either a single line or a chunk of multiple lines.
        No-op if the task is unknown or already finished.
        """
        tracked = self._tasks.get(task_id)
        if tracked is None or tracked.status != TaskStatus.RUNNING:
            return
        for piece in str(line).splitlines() or [""]:
            tracked.progress_lines.append(piece)

    def set_progress_provider(self, task_id: str, provider: Callable[[int], str]) -> None:
        """Register a pull-callback for live progress on *task_id*.

        The provider is invoked synchronously by background result tools while
        the task is still running. It should return a compact text snapshot of
        the task's current state.
        """
        tracked = self._tasks.get(task_id)
        if tracked is not None:
            tracked.progress_provider = provider

    def make_progress_callback(self, task_id: str) -> Callable[[str], None]:
        """Return a callable that appends progress lines for *task_id*.

        Convenience for wiring into a tool's execution context — the tool
        can call ``ctx['on_progress_line']('hello')`` without ever
        knowing about the manager.
        """
        return lambda line: self.append_progress(task_id, line)

    async def cancel(self, task_id: str, reason: str = "") -> bool:
        """Cancel a task by id. Returns True if found and cancelled.

        Subagents receive a cooperative early-stop cancellation so they can
        salvage a partial result. Ordinary background tools are pure-Python
        jobs and are cancelled through their asyncio task.

        Race-safe via the terminal-status latch: if the task already
        completed (e.g. a 1 s shell that exited just before the user clicked
        cancel), the COMPLETED result is preserved.
        """
        tracked = self._tasks.get(task_id)
        if tracked is None:
            return False
        tracked.cancel_reason = reason or None
        await self._wire_cancel_if_sandbox_bound(tracked)
        if not should_cancel_asyncio_task(tracked):
            await request_subagent_early_stop(tracked, reason=reason)
            return True
        tracked.stop_mode = "cancel"
        msg = f"Cancelled: {reason}" if reason else "Cancelled"
        applied = self._set_terminal_status(
            tracked,
            new_status=TaskStatus.CANCELLED,
            new_result=ToolResult(output=msg, is_error=True),
        )
        if applied:
            tracked.progress_lines = [msg]
        tracked.asyncio_task.cancel()
        self._stop_heartbeat_if_idle()
        return True

    async def cancel_by_agent(self, agent_id: str, *, grace_s: float) -> int:
        """Cancel running sandbox-bound background tasks for one agent.

        Returns the number of asyncio tasks still not done after ``grace_s``.
        """
        targets = [
            tracked
            for tracked in self._tasks.values()
            if tracked.status == TaskStatus.RUNNING
            and tracked.uses_sandbox
            and tracked.agent_id == agent_id
        ]
        if not targets:
            return 0
        await asyncio.gather(
            *(self.cancel(tracked.task_id, reason="isolated_workspace_exit") for tracked in targets),
            return_exceptions=True,
        )
        pending = [tracked.asyncio_task for tracked in targets if not tracked.asyncio_task.done()]
        if pending and grace_s > 0:
            _, still_pending = await asyncio.wait(pending, timeout=grace_s)
            pending = list(still_pending)
        for task in pending:
            task.cancel()
        return len([task for task in pending if not task.done()])

    def get_task(self, task_id: str) -> TrackedBackgroundTask | None:
        """Return the tracked task for *task_id* (or None)."""
        return self._tasks.get(task_id)

    async def cancel_all(self) -> None:
        """Cancel all running tasks. Called on query loop exit."""
        cancelled_tasks: list[asyncio.Task[ToolResult]] = []
        for tracked in self._tasks.values():
            if tracked.status != TaskStatus.RUNNING:
                continue
            tracked.stop_mode = "cancel"
            applied = self._set_terminal_status(
                tracked,
                new_status=TaskStatus.CANCELLED,
                new_result=ToolResult(output="Cancelled", is_error=True),
            )
            if applied:
                tracked.progress_lines = ["Cancelled"]
            await self._wire_cancel_if_sandbox_bound(tracked)
            if should_cancel_asyncio_task(tracked):
                tracked.asyncio_task.cancel()
                cancelled_tasks.append(tracked.asyncio_task)
        if cancelled_tasks:
            await asyncio.gather(*cancelled_tasks, return_exceptions=True)
        self._stop_heartbeat_if_idle()

    def _set_terminal_status(
        self,
        tracked: TrackedBackgroundTask,
        *,
        new_status: TaskStatus,
        new_result: ToolResult | None,
    ) -> bool:
        """CAS one terminal-status transition. Returns ``True`` if applied.

        Precedence: ``completed > failed > cancelled > running``. ``delivered``
        is the post-terminal sink; nothing overwrites it. The lock here is
        cheap (per-task, never contended outside of cancel races) and makes
        the precedence rule deterministic even if event-loop ordering
        re-shuffles cancel + done_callback.
        """
        new_rank = _TERMINAL_PRECEDENCE.get(new_status.value, 0)
        with tracked._terminal_lock:
            current_rank = _TERMINAL_PRECEDENCE.get(tracked.status.value, 0)
            if new_rank <= current_rank:
                return False
            tracked.status = new_status
            if new_result is not None:
                tracked.result = new_result
            return True

    async def _wire_cancel_if_sandbox_bound(self, tracked: TrackedBackgroundTask) -> None:
        if not tracked.uses_sandbox or not tracked.sandbox_id or not tracked.sandbox_invocation_id:
            return
        try:
            import sandbox.api as sandbox_api

            await sandbox_api.cancel(tracked.sandbox_id, tracked.sandbox_invocation_id)
        except Exception as exc:
            logger.warning(
                "wire-cancel failed for task_id=%s invocation_id=%s: %s",
                tracked.task_id,
                tracked.sandbox_invocation_id,
                exc,
            )

    def _ensure_heartbeat_task(self) -> None:
        if self._heartbeat_task is not None and not self._heartbeat_task.done():
            return
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    def _stop_heartbeat_if_idle(self) -> None:
        if self._running_sandbox_invocation_ids():
            return
        if self._heartbeat_task is not None and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
        self._heartbeat_task = None

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(_HEARTBEAT_INTERVAL_S)
            by_sandbox = self._running_sandbox_invocation_ids()
            if not by_sandbox:
                self._heartbeat_task = None
                return
            try:
                import sandbox.api as sandbox_api

                await asyncio.gather(
                    *(
                        sandbox_api.heartbeat(
                            sandbox_id,
                            invocation_ids,
                            engine_process_id=self._engine_process_id,
                            engine_started_at=self._engine_started_at,
                        )
                        for sandbox_id, invocation_ids in by_sandbox.items()
                    ),
                    return_exceptions=True,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug("background heartbeat iteration failed", exc_info=True)

    def _running_sandbox_invocation_ids(self) -> dict[str, list[str]]:
        by_sandbox: dict[str, list[str]] = {}
        for tracked in self._tasks.values():
            if (
                tracked.status == TaskStatus.RUNNING
                and tracked.uses_sandbox
                and tracked.sandbox_id
                and tracked.sandbox_invocation_id
            ):
                by_sandbox.setdefault(tracked.sandbox_id, []).append(
                    tracked.sandbox_invocation_id
                )
        return by_sandbox
