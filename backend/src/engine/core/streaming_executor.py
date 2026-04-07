"""Streaming tool executor for mid-stream tool detection and abort support."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from message.messages import ConversationMessage
from message.stream_events import (
    ToolExecutionCancelled,
    ToolExecutionCompleted,
    ToolExecutionProgress,
    ToolExecutionStarted,
)
from tools.core.base import ToolExecutionContext, ToolRegistry, ToolResult

if TYPE_CHECKING:
    from providers.types import ApiToolUseDeltaEvent

logger = logging.getLogger(__name__)


@dataclass
class TrackedTool:
    id: str
    name: str
    input: dict[str, Any]
    assistant_message: ConversationMessage
    status: str = "queued"
    is_concurrency_safe: bool = True
    task: asyncio.Task | None = None
    progress_lines: list[str] = field(default_factory=list)
    result: ToolResult | None = None
    cancelled: bool = False
    cancel_reason: str = ""


class StreamingToolExecutor:
    """Executes tools as they arrive mid-stream with progress support.

    Features:
    - Tools start executing as soon as tool_use blocks arrive (mid-stream)
    - Concurrency-safe tools run in parallel
    - Progress events stream back for long-running operations
    - LLM can abort tools via cancel() signal
    """

    def __init__(
        self,
        tool_registry: ToolRegistry,
        context: ToolExecutionContext,
    ):
        self._tool_registry = tool_registry
        self._context = context
        self._tools: dict[str, TrackedTool] = {}
        self._aborted: set[str] = set()
        self._skipped_background: set[str] = set()

    @property
    def skipped_background_ids(self) -> set[str]:
        """IDs of tools that were skipped because they requested background execution."""
        return self._skipped_background

    def add_tool(
        self, event: ApiToolUseDeltaEvent, assistant_message: ConversationMessage
    ) -> ToolExecutionStarted | None:
        """Add a tool to execute as it arrives mid-stream. Returns started event if tool was started."""
        tool_def = self._tool_registry.get(event.name)

        # Skip tools requesting background execution — they'll be handled
        # by the BackgroundTaskManager in the query loop instead. Tools with
        # force_background=True (e.g. run_subagent) are ALWAYS dispatched in
        # the background, regardless of the LLM's input.
        force_bg = bool(getattr(tool_def, "force_background", False))
        wants_bg = bool(event.input and event.input.get("background"))
        if tool_def and tool_def.supports_background and (force_bg or wants_bg):
            self._skipped_background.add(event.id)
            logger.info(
                "STREAM: Skipping background tool: tool_id=%s tool_name=%s",
                event.id,
                event.name,
            )
            return None

        # Determine concurrency safety. If the LLM sent invalid input, defer
        # the ValidationError to _execute_tool (which returns it as a tool
        # error to the LLM) instead of crashing the query loop here.
        is_concurrency_safe = False
        if tool_def:
            try:
                is_concurrency_safe = tool_def.is_read_only(
                    tool_def.input_model.model_validate(event.input)
                )
            except Exception as exc:
                logger.warning(
                    "STREAM: Invalid tool input for %s, deferring error: %s",
                    event.name,
                    exc,
                )
        tracked = TrackedTool(
            id=event.id,
            name=event.name,
            input=event.input,
            assistant_message=assistant_message,
            is_concurrency_safe=is_concurrency_safe,
        )
        self._tools[event.id] = tracked
        logger.debug(
            "STREAM: Received tool_use event: tool_id=%s tool_name=%s concurrency_safe=%s input=%s",
            event.id,
            event.name,
            tracked.is_concurrency_safe,
            event.input,
        )
        if event.input is not None:
            self._start_tool(tracked)
            logger.info("STREAM: Tool started: tool_id=%s tool_name=%s", event.id, event.name)
            return ToolExecutionStarted(tool_name=event.name, tool_input=event.input)
        return None

    def cancel(self, tool_id: str, reason: str) -> None:
        """Cancel a running tool."""
        logger.info("STREAM: Cancel requested: tool_id=%s reason=%s", tool_id, reason)
        self._aborted.add(tool_id)
        if tool_id in self._tools:
            self._tools[tool_id].cancelled = True
            self._tools[tool_id].cancel_reason = reason
            task = self._tools[tool_id].task
            if task and not task.done():
                task.cancel()
                logger.info("STREAM: Cancel signal sent: tool_id=%s", tool_id)

    def get_progress(self) -> list[ToolExecutionProgress]:
        """Get new progress events since last call."""
        events = []
        for tool in self._tools.values():
            if tool.status == "completed" and tool.progress_lines:
                for line in tool.progress_lines:
                    events.append(
                        ToolExecutionProgress(
                            tool_id=tool.id,
                            tool_name=tool.name,
                            output=line,
                        )
                    )
                tool.progress_lines.clear()
        return events

    async def get_remaining(self) -> list[ToolExecutionCompleted | ToolExecutionCancelled]:
        """Get final results after stream completes.

        Waits for any in-flight tools to finish before returning.
        This prevents the race where MiniMax sends tool_use + complete
        together and the tool hasn't finished executing yet.
        """
        # Wait for in-flight tools to finish
        in_flight = [
            tool.task
            for tool in self._tools.values()
            if tool.status == "executing" and tool.task is not None
        ]
        if in_flight:
            await asyncio.gather(*in_flight, return_exceptions=True)

        results = []
        for tool in self._tools.values():
            if tool.status == "completed":
                if tool.cancelled:
                    results.append(
                        ToolExecutionCancelled(
                            tool_id=tool.id,
                            tool_name=tool.name,
                            reason=tool.cancel_reason or "Cancelled by LLM",
                        )
                    )
                elif tool.result:
                    results.append(
                        ToolExecutionCompleted(
                            tool_name=tool.name,
                            output=tool.result.output,
                            is_error=tool.result.is_error,
                            tool_id=tool.id,
                        )
                    )
                tool.status = "yielded"
        return results

    def _start_tool(self, tool: TrackedTool) -> None:
        """Start executing a tool."""
        tool.status = "executing"
        tool.task = asyncio.create_task(self._execute_tool(tool))

    async def _execute_tool(self, tool: TrackedTool) -> None:
        """Execute a single tool with progress tracking."""
        logger.info("STREAM: Executing tool: tool_id=%s tool_name=%s", tool.id, tool.name)
        try:
            if tool.id in self._aborted:
                logger.info("STREAM: Tool aborted before execution: tool_id=%s", tool.id)
                tool.status = "completed"
                tool.cancelled = True
                return

            tool_def = self._tool_registry.get(tool.name)
            if not tool_def:
                logger.warning("STREAM: Unknown tool: tool_id=%s tool_name=%s", tool.id, tool.name)
                tool.result = ToolResult(
                    output=f"Unknown tool: {tool.name}",
                    is_error=True,
                )
                tool.status = "completed"
                return

            parsed_input = tool_def.input_model.model_validate(tool.input)

            context_with_id = ToolExecutionContext(
                cwd=self._context.cwd,
                metadata={**self._context.metadata, "tool_id": tool.id},
            )

            tool.result = await tool_def.execute(parsed_input, context_with_id)
            logger.info(
                "STREAM: Tool completed: tool_id=%s tool_name=%s is_error=%s output_len=%d",
                tool.id,
                tool.name,
                tool.result.is_error,
                len(tool.result.output) if tool.result.output else 0,
            )
        except asyncio.CancelledError:
            logger.info("STREAM: Tool cancelled during execution: tool_id=%s", tool.id)
            tool.cancelled = True
            tool.cancel_reason = tool.cancel_reason or "Task cancelled"
        except ValidationError as exc:
            logger.warning(
                "STREAM: Tool input validation failed: tool_id=%s tool_name=%s error=%s",
                tool.id,
                tool.name,
                exc,
            )
            errors = "; ".join(
                f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
            )
            tool.result = ToolResult(
                output=(
                    f"Invalid input for {tool.name}: {errors}. "
                    "Please retry the tool call with valid arguments."
                ),
                is_error=True,
            )
        except Exception as exc:
            logger.error(
                "STREAM: Tool execution error: tool_id=%s error=%s", tool.id, exc, exc_info=True
            )
            tool.result = ToolResult(
                output=f"Tool execution failed: {exc}",
                is_error=True,
            )
        finally:
            tool.status = "completed"

    def get_started_events(self) -> list[ToolExecutionStarted]:
        """Get ToolExecutionStarted events for all queued tools."""
        return [
            ToolExecutionStarted(tool_name=t.name, tool_input=t.input)
            for t in self._tools.values()
            if t.status == "queued"
        ]

    def cancel_all(self) -> None:
        """Cancel all running tasks to prevent orphaned execution."""
        for tool in self._tools.values():
            if tool.task and not tool.task.done():
                tool.task.cancel()
                tool.cancelled = True
                tool.cancel_reason = "Superseded by fallback execution"
