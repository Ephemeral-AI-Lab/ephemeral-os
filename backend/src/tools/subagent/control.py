"""Typed subagent progress and cancellation tools."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from engine.background.task_supervisor import (
    BackgroundTaskRecord,
    BackgroundTaskStatus,
    BackgroundTaskSupervisor,
)
from tools._framework.core.base import (
    BaseTool,
    TextToolOutput,
    ToolExecutionContextService,
    ToolResult,
)


class CheckSubagentProgressInput(BaseModel):
    subagent_session_id: str = Field(..., min_length=1)
    last_n_messages: int = Field(default=5, ge=1, le=10)


class CancelSubagentInput(BaseModel):
    subagent_session_id: str = Field(..., min_length=1)
    reason: str = Field(default="")


def _get_manager(
    context: ToolExecutionContextService,
) -> BackgroundTaskSupervisor | None:
    manager = context.get("background_task_manager")
    if isinstance(manager, BackgroundTaskSupervisor):
        return manager
    return None


def _peek_messages(tracked: BackgroundTaskRecord, n: int) -> str:
    provider = tracked.progress_provider
    if provider is None:
        return "(no progress snapshot available)"
    try:
        return str(provider(n))
    except Exception as exc:
        return f"[progress provider error: {exc}]"


def _terminal_called(tracked: BackgroundTaskRecord) -> bool:
    result = tracked.result
    metadata = result.metadata if result is not None else {}
    return bool(metadata.get("subagent_terminal_called"))


def _result_metadata(tracked: BackgroundTaskRecord) -> dict[str, Any]:
    result = tracked.result
    metadata = result.metadata if result is not None else {}
    return dict(metadata or {}) if isinstance(metadata, dict) else {}


def _subagent_status_and_result(
    tracked: BackgroundTaskRecord,
    *,
    last_n_messages: int,
) -> tuple[str, str]:
    raw_status = str(tracked.status)
    metadata = _result_metadata(tracked)
    termination_reason = metadata.get("subagent_termination_reason")
    if termination_reason:
        return (
            "terminated",
            f"[terminated: {termination_reason}] {_peek_messages(tracked, last_n_messages)}",
        )
    if metadata.get("subagent_cancelled"):
        return "cancelled", f"[cancelled] {_peek_messages(tracked, last_n_messages)}"
    if raw_status == BackgroundTaskStatus.RUNNING.value:
        return "running", _peek_messages(tracked, last_n_messages)
    if raw_status in {
        BackgroundTaskStatus.COMPLETED.value,
        BackgroundTaskStatus.DELIVERED.value,
    } and _terminal_called(tracked):
        result = tracked.result
        return "finished", result.output if result is not None else ""
    if raw_status == BackgroundTaskStatus.CANCELLED.value:
        return "cancelled", f"[cancelled] {_peek_messages(tracked, last_n_messages)}"
    return "failed", _peek_messages(tracked, last_n_messages)


class CheckSubagentProgressTool(BaseTool):
    name = "check_subagent_progress"
    description = (
        "Check a running or finished subagent by subagent_session_id. Returns "
        "the latest child-agent message snapshot while running and the terminal "
        "result after successful completion."
    )
    short_description = "Check subagent progress."
    input_model = CheckSubagentProgressInput
    output_model = TextToolOutput

    async def execute(
        self,
        arguments: BaseModel,
        context: ToolExecutionContextService,
    ) -> ToolResult:
        manager = _get_manager(context)
        if manager is None:
            return ToolResult(
                output="ERROR: subagent session manager is not available.",
                is_error=True,
            )
        assert isinstance(arguments, CheckSubagentProgressInput)
        tracked = manager.get_subagent_task(arguments.subagent_session_id)
        if tracked is None:
            return ToolResult(
                output=(
                    "No subagent session found with ID: "
                    f"{arguments.subagent_session_id}"
                ),
                is_error=True,
            )

        status, result = _subagent_status_and_result(
            tracked,
            last_n_messages=arguments.last_n_messages,
        )
        if status != "running" and str(tracked.status) in {
            BackgroundTaskStatus.COMPLETED.value,
            BackgroundTaskStatus.FAILED.value,
            BackgroundTaskStatus.CANCELLED.value,
        }:
            manager.mark_subagent_delivered(arguments.subagent_session_id)

        payload = {
            "subagent_session_id": arguments.subagent_session_id,
            "status": status,
            "agent_name": str(tracked.tool_input.get("agent_name") or ""),
            "result": result,
        }
        return ToolResult(
            output=json.dumps(payload, indent=2),
            is_error=False,
            metadata={"subagent_snapshot": payload},
        )


class CancelSubagentTool(BaseTool):
    name = "cancel_subagent"
    description = "Cancel a running subagent by subagent_session_id."
    short_description = "Cancel subagent."
    input_model = CancelSubagentInput
    output_model = TextToolOutput

    async def execute(
        self,
        arguments: BaseModel,
        context: ToolExecutionContextService,
    ) -> ToolResult:
        manager = _get_manager(context)
        if manager is None:
            return ToolResult(
                output="ERROR: subagent session manager is not available.",
                is_error=True,
            )
        assert isinstance(arguments, CancelSubagentInput)
        cancelled = await manager.cancel_subagent_session(
            arguments.subagent_session_id,
            arguments.reason,
        )
        if not cancelled:
            return ToolResult(
                output=(
                    "Could not cancel subagent session "
                    f"{arguments.subagent_session_id}. It may have already "
                    "completed or does not exist."
                ),
                is_error=True,
            )
        reason = f" Reason: {arguments.reason}" if arguments.reason else ""
        return ToolResult(
            output=(
                f"Subagent session {arguments.subagent_session_id} "
                f"cancellation requested.{reason}"
            ),
            is_error=False,
        )


def make_subagent_control_tools() -> list[BaseTool]:
    return [CheckSubagentProgressTool(), CancelSubagentTool()]


__all__ = [
    "CancelSubagentInput",
    "CancelSubagentTool",
    "CheckSubagentProgressInput",
    "CheckSubagentProgressTool",
    "make_subagent_control_tools",
]
