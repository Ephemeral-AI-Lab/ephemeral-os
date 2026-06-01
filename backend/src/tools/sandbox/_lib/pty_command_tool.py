"""Shared helpers for PTY command control tools."""

from __future__ import annotations

import json

from pydantic import BaseModel

from sandbox.shared.models import ExecCommandResult
from tools._framework.core.base import ToolExecutionContextService, ToolResult


class PtyCommandOutput(BaseModel):
    status: str
    exit_code: int | None
    output: dict[str, str]
    pty_session_id: str | None = None


def pty_tool_result(result: ExecCommandResult) -> ToolResult:
    payload = {
        "status": result.status,
        "exit_code": result.exit_code,
        "output": {
            "stdout": result.output.stdout,
            "stderr": result.output.stderr,
        },
    }
    if result.pty_session_id:
        payload["pty_session_id"] = result.pty_session_id
    return ToolResult(
        output=json.dumps(payload),
        is_error=result.status == "error",
        metadata={"status": result.status, "pty_session_id": result.pty_session_id or ""},
    )


def mark_pty_result_reported_by_tool(
    context: ToolExecutionContextService,
    result: ExecCommandResult,
    *,
    pty_session_id: str | None = None,
) -> None:
    session_id = result.pty_session_id or pty_session_id
    if not session_id or result.status == "running":
        return
    if result.status == "error" and result.pty_session_id is None:
        return
    manager = context.get("background_task_manager")
    mark = getattr(manager, "mark_pty_result_reported_by_tool", None)
    if not callable(mark):
        return
    mark(
        pty_session_id=session_id,
        result={
            "status": result.status,
            "exit_code": result.exit_code,
            "output": {
                "stdout": result.output.stdout,
                "stderr": result.output.stderr,
            },
        },
    )


__all__ = [
    "PtyCommandOutput",
    "mark_pty_result_reported_by_tool",
    "pty_tool_result",
]
