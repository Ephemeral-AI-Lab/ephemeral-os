"""Built-in tool for blocking until background tasks complete."""

from __future__ import annotations

import json
import time

from pydantic import BaseModel, Field, model_validator

from tools.core.base import BaseTool, ToolExecutionContext, ToolResult


class WaitForBackgroundTaskInput(BaseModel):
    """Input for wait_for_background_task tool."""
    task_id: str = Field(
        ...,
        description=(
            "REQUIRED. Either the exact `task_id` string (e.g. \"bg_1\") shown "
            "in the `[BACKGROUND LAUNCHED]` message / `check_background_progress` "
            "output, OR the literal string \"all\" to wait for every pending "
            "background task. Never pass null/None and never omit this field. "
            "If you do not know the id, call `check_background_progress` first."
        ),
    )
    timeout: float = Field(
        default=30,
        description="Maximum seconds to block waiting. Capped at 300s server-side. Minimum 1s.",
    )
    last_n_lines: int = Field(
        default=20,
        description="Number of output lines to include for completed tasks.",
    )

    @model_validator(mode="after")
    def _validate_task_id(self) -> "WaitForBackgroundTaskInput":
        if not isinstance(self.task_id, str) or not self.task_id:
            raise ValueError(
                "task_id must be a non-empty string: either \"bg_N\" or \"all\"."
            )
        return self


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
        assert isinstance(arguments, WaitForBackgroundTaskInput)

        # Defensive: schema validation already rejects None / "" / non-str,
        # but guard here too so a buggy caller bypassing validation gets a
        # clear error instead of an attribute traceback.
        if arguments.task_id is None or not isinstance(arguments.task_id, str) or not arguments.task_id:
            return ToolResult(
                output=(
                    "ERROR: task_id is required and must be a non-empty string. "
                    "Pass either an exact id like \"bg_1\" or \"all\" to wait "
                    "for every pending background task."
                ),
                is_error=True,
            )

        manager = context.metadata.get("background_task_manager")
        if manager is None:
            return ToolResult(
                output=(
                    "ERROR: background task manager is not available in this "
                    "context — no background tasks can be waited on."
                ),
                is_error=True,
            )

        timeout = max(1.0, min(arguments.timeout, 300.0))
        wait_for_all = arguments.task_id == "all"
        target_id: str | None = None if wait_for_all else arguments.task_id

        # ---- task_id="all" branch ----
        if wait_for_all:
            snapshot = manager.get_status()
            running = [s for s in snapshot if s.get("status") == "running"]
            running_count = len(running)

            if running_count == 0:
                finished = [
                    s for s in snapshot
                    if s.get("status") in ("completed", "failed", "cancelled", "delivered")
                ]
                if finished:
                    _apply_last_n_lines(finished, arguments.last_n_lines)
                    return ToolResult(
                        output=(
                            "[NO TASKS RUNNING] 0 background tasks are pending. "
                            "All previously launched tasks have already finished; "
                            "their results were (or will be) delivered as "
                            "[BACKGROUND <task_id> COMPLETED] messages.\n"
                            f"{json.dumps(finished, indent=2)}"
                        ),
                        is_error=False,
                    )
                return ToolResult(
                    output=(
                        "[NO TASKS RUNNING] 0 background tasks are pending and "
                        "none have ever been launched in this session."
                    ),
                    is_error=False,
                )

            if running_count == 1:
                # Exactly one running — auto-target it so the wait loop reports
                # against the single task instead of using "all" semantics.
                target_id = running[0]["task_id"]
                wait_for_all = False

        # ---- specific task_id branch ----
        if target_id is not None:
            task_statuses = manager.get_status(target_id)
            if not task_statuses:
                return ToolResult(
                    output=f"No background task found with ID: {target_id}",
                    is_error=True,
                )
            if task_statuses[0].get("status") != "running":
                _apply_last_n_lines(task_statuses, arguments.last_n_lines)
                return ToolResult(
                    output=f"[COMPLETED]\n{json.dumps(task_statuses, indent=2)}",
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

            if wait_for_all:
                if not manager.has_pending():
                    break
                continue
            task_statuses = manager.get_status(target_id)
            if not task_statuses or task_statuses[0].get("status") != "running":
                break

        elapsed = time.monotonic() - start
        status = manager.get_status(target_id)
        _apply_last_n_lines(status, arguments.last_n_lines)

        # Determine if timed out
        if wait_for_all:
            timed_out = manager.has_pending()
        else:
            task_statuses = manager.get_status(target_id)
            timed_out = bool(task_statuses) and task_statuses[0].get("status") == "running"

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
