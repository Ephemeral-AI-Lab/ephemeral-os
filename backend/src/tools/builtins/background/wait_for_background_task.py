"""Built-in tool for blocking until background tasks complete."""

from __future__ import annotations

import json
import time

from pydantic import BaseModel, Field

from tools.core.base import BaseTool, ToolExecutionContext, ToolResult


class WaitForBackgroundTaskInput(BaseModel):
    """Input for wait_for_background_task tool."""
    task_id: str | None = Field(
        default=None,
        description="Optional task ID to wait for. If omitted, waits for any task to complete.",
    )
    timeout: float = Field(
        default=30,
        description="Maximum seconds to block waiting. Capped at 300s server-side. Minimum 1s.",
    )
    wait_for_all: bool = Field(
        default=False,
        description="If True, wait until ALL pending tasks complete, not just the first.",
    )
    last_n_lines: int = Field(
        default=20,
        description="Number of output lines to include for completed tasks.",
    )


class WaitForBackgroundTaskTool(BaseTool):
    """Block until background task(s) complete or timeout.

    Suspends execution server-side so the LLM does not need to poll in tight
    loops. Use this only when there is no foreground work to do.
    """

    name: str = "wait_for_background_task"
    description: str = (
        "Block server-side until background task(s) complete or the timeout expires. "
        "Use this ONLY when you have no foreground work to do and need to wait for "
        "background tasks. Always call check_background_progress first to review task "
        "status before using this tool."
    )
    input_model: type[BaseModel] = WaitForBackgroundTaskInput

    async def execute(self, arguments: BaseModel, context: ToolExecutionContext) -> ToolResult:
        manager = context.metadata.get("background_task_manager")
        if manager is None:
            return ToolResult(output="No background tasks are running.", is_error=False)

        assert isinstance(arguments, WaitForBackgroundTaskInput)

        timeout = max(1.0, min(arguments.timeout, 300.0))

        # Validate task_id if provided
        if arguments.task_id is not None:
            task_statuses = manager.get_status(arguments.task_id)
            if not task_statuses:
                return ToolResult(
                    output=f"No background task found with ID: {arguments.task_id}",
                    is_error=True,
                )
            # If already completed, return status immediately
            if task_statuses[0].get("status") != "running":
                status = manager.get_status(arguments.task_id)
                _apply_last_n_lines(status, arguments.last_n_lines)
                prefix = "[COMPLETED]"
                return ToolResult(
                    output=f"{prefix}\n{json.dumps(status, indent=2)}",
                    is_error=False,
                )

        # If no pending tasks, return early
        if not manager.has_pending():
            status = manager.get_status(arguments.task_id)
            _apply_last_n_lines(status, arguments.last_n_lines)
            return ToolResult(
                output=f"All tasks already completed.\n{json.dumps(status, indent=2)}",
                is_error=False,
            )

        # Wait loop
        start = time.monotonic()
        while True:
            elapsed = time.monotonic() - start
            remaining = timeout - elapsed
            if remaining <= 0:
                break

            await manager.wait_any(timeout=remaining)

            if arguments.wait_for_all:
                if not manager.has_pending():
                    break
                # remaining recalculated at top of loop
                continue
            elif arguments.task_id is not None:
                task_statuses = manager.get_status(arguments.task_id)
                if not task_statuses or task_statuses[0].get("status") != "running":
                    break
                continue
            else:
                break

        elapsed = time.monotonic() - start
        status = manager.get_status(arguments.task_id)
        _apply_last_n_lines(status, arguments.last_n_lines)

        # Determine if timed out
        timed_out = False
        if arguments.task_id is not None:
            task_statuses = manager.get_status(arguments.task_id)
            if task_statuses and task_statuses[0].get("status") == "running":
                timed_out = True
        elif manager.has_pending():
            timed_out = True

        if timed_out:
            hint = (
                "Call wait_for_background_task again to continue waiting, "
                "or cancel_background_task to stop."
            )
            output = f"[TIMED_OUT after {elapsed:.1f}s]\n{json.dumps(status, indent=2)}\n{hint}"
        else:
            output = f"[COMPLETED]\n{json.dumps(status, indent=2)}"

        return ToolResult(output=output, is_error=False)

    def is_read_only(self, arguments: BaseModel) -> bool:
        return True


def _apply_last_n_lines(status: list[dict], last_n_lines: int) -> None:
    """Truncate 'output' field in each status entry to the last N lines, in-place."""
    for entry in status:
        if "output" in entry and isinstance(entry["output"], str):
            lines = entry["output"].splitlines()
            if len(lines) > last_n_lines:
                entry["output"] = "\n".join(lines[-last_n_lines:])
