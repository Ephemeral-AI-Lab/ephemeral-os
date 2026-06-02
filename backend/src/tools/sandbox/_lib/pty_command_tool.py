"""Shared helpers for command and PTY control tools."""

from __future__ import annotations

import json

from pydantic import BaseModel, Field

from sandbox.shared.models import ExecCommandResult
from tools._framework.core.base import ToolExecutionContextService, ToolResult


class CommandToolOutput(BaseModel):
    status: str
    exit_code: int | None
    output: dict[str, str]
    pty_session_id: str | None = None
    stdout: str = ""
    stderr: str = ""
    changed_paths: list[str] = Field(default_factory=list)
    changed_path_kinds: dict[str, str] = Field(default_factory=dict)
    mutation_source: str = ""
    conflict_reason: str | None = None
    error: dict[str, object] | None = None


def command_result_payload(result: ExecCommandResult) -> dict[str, object]:
    payload = {
        "status": result.status,
        "exit_code": result.exit_code,
        "output": {
            "stdout": result.output.stdout,
            "stderr": result.output.stderr,
        },
        "stdout": result.output.stdout,
        "stderr": result.output.stderr,
        "changed_paths": list(result.changed_paths),
        "changed_path_kinds": dict(result.changed_path_kinds),
        "mutation_source": result.mutation_source,
        "conflict_reason": result.conflict_reason,
    }
    if result.pty_session_id:
        payload["pty_session_id"] = result.pty_session_id
    if result.error:
        payload["error"] = dict(result.error)
    return payload


def command_tool_result(result: ExecCommandResult) -> ToolResult:
    payload = command_result_payload(result)
    return ToolResult(
        output=json.dumps(payload),
        is_error=result.status in {"error", "timed_out"},
        metadata={
            "status": result.status,
            "pty_session_id": result.pty_session_id or "",
            "changed_paths": list(result.changed_paths),
            "changed_path_kinds": dict(result.changed_path_kinds),
            "mutation_source": result.mutation_source,
            "conflict_reason": result.conflict_reason,
        },
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
    payload = command_result_payload(result)
    mark(
        pty_session_id=session_id,
        result=payload,
    )


__all__ = [
    "CommandToolOutput",
    "command_result_payload",
    "command_tool_result",
    "mark_pty_result_reported_by_tool",
]
