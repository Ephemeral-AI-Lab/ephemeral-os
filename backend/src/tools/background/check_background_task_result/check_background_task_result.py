"""Built-in tool for fetching the result of a single background task."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from tools._framework.core.base import (
    BaseTool,
    TextToolOutput,
    ToolExecutionContextService,
    ToolResult,
)
from .prompt import get_check_background_task_result_description

from tools.background._lib.task_output import (
    BACKGROUND_TASK_ID_FIELD,
    background_task_display_status,
    render_background_tool_call,
)


class CheckBackgroundTaskResultInput(BaseModel):
    """Input for check_background_task_result tool."""
    task_id: str = BACKGROUND_TASK_ID_FIELD


def _build_generic_result(tracked, raw_status: str) -> str:
    """Return result text for non-subagent tools (e.g. shell).

    No truncation — shell output is returned verbatim.
    """
    if raw_status == "running":
        if tracked.progress_lines:
            return "\n".join(tracked.progress_lines)
        return "[no output captured yet]"
    if tracked.result is None:
        return ""
    return tracked.result.output or ""


class CheckBackgroundTaskResultTool(BaseTool):
    """Fetch the current result of a single background task.

    Returns a JSON object: ``{id, status, tool_command, result}``.
    Works on running tasks (returns a snapshot) and on terminal tasks.
    """

    name: str = "check_background_task_result"
    description: str = get_check_background_task_result_description()
    short_description: str = "Check a background task's result."
    input_model: type[BaseModel] = CheckBackgroundTaskResultInput
    output_model: type[BaseModel] = TextToolOutput

    async def execute(self, arguments: BaseModel, context: ToolExecutionContextService) -> ToolResult:
        manager = context.get("background_task_manager")
        if manager is None:
            return ToolResult(
                output="ERROR: background task manager is not available.",
                is_error=True,
            )

        assert isinstance(arguments, CheckBackgroundTaskResultInput)
        tracked = manager.get_task(arguments.task_id) if hasattr(manager, "get_task") else None
        if tracked is None:
            return ToolResult(
                output=f"No background task found with ID: {arguments.task_id}",
                is_error=True,
            )
        if getattr(tracked, "task_type", "") == "subagent":
            return ToolResult(
                output=(
                    "Subagent sessions are not managed by "
                    "check_background_task_result. Use "
                    "check_subagent_progress(subagent_session_id=...) instead."
                ),
                is_error=True,
            )

        raw_status = str(tracked.status)
        tool_command = render_background_tool_call(
            tracked.tool_name,
            tracked.tool_input,
        )
        status = background_task_display_status(raw_status)
        result = _build_generic_result(tracked, raw_status)

        # If the engine hasn't yet delivered this terminal task, mark it
        # delivered now so we don't get a duplicate [BACKGROUND COMPLETED]
        # message — the caller already has the result in this response.
        if status != "running" and raw_status in ("completed", "failed", "cancelled"):
            manager.collect_completed()

        payload: dict[str, Any] = {
            "id": tracked.task_id,
            "status": status,
            "tool_command": tool_command,
            "result": result,
        }
        return ToolResult(output=json.dumps(payload, indent=2), is_error=False)
