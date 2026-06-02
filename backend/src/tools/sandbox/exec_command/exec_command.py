"""Phase 3T sandbox command tool."""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

from pydantic import BaseModel, Field

import sandbox.api as sandbox_api
from sandbox.shared.models import ExecCommandRequest, Intent
from tools._framework.core.base import ToolExecutionContextService, ToolResult
from tools._framework.core.decorator import tool
from tools._hooks.destructive_shell import (
    DestructiveGitShellPreHook,
    DestructiveShellPreHook,
)
from tools.sandbox._lib.pty_command_tool import (
    CommandToolOutput,
    command_tool_result,
)
from tools.sandbox._lib.tool_context import (
    sandbox_audit_metadata_from_tool_context,
    sandbox_audit_kwargs_from_tool_context,
    sandbox_caller_from_tool_context,
    sandbox_id_or_missing_error_result,
)


class ExecCommandInput(BaseModel):
    cmd: str = Field(..., min_length=1)
    tty: bool = False
    yield_time_ms: int = Field(default=1000, ge=0, le=30_000)
    timeout: int = Field(default=900, ge=1)


@tool(
    name="exec_command",
    description="Run a sandbox command, optionally as an interactive PTY session.",
    short_description="Run command.",
    input_model=ExecCommandInput,
    output_model=CommandToolOutput,
    intent=Intent.WRITE_ALLOWED,
    pre_hooks=(
        DestructiveGitShellPreHook("exec_command"),
        DestructiveShellPreHook("exec_command"),
    ),
)
async def exec_command(
    cmd: str,
    tty: bool = False,
    yield_time_ms: int = 1000,
    timeout: int = 900,
    *,
    context: ToolExecutionContextService,
) -> ToolResult:
    sandbox_id, sandbox_id_error = sandbox_id_or_missing_error_result(context)
    if sandbox_id_error is not None:
        return sandbox_id_error
    caller = sandbox_caller_from_tool_context(context)
    invocation_id = str(context.get("sandbox_invocation_id") or uuid4().hex)
    result = await sandbox_api.exec_command(
        sandbox_id,
        ExecCommandRequest(
            invocation_id=invocation_id,
            cmd=cmd,
            tty=tty,
            yield_time_ms=yield_time_ms,
            timeout=timeout,
            caller=caller,
            description="exec_command",
        ),
        **sandbox_audit_kwargs_from_tool_context(context),
    )
    if result.pty_session_id:
        manager = context.get("background_task_manager")
        register = getattr(manager, "register_pty_command", None)
        if callable(register):
            register(
                pty_session_id=result.pty_session_id,
                sandbox_id=sandbox_id,
                agent_id=caller.agent_id,
                command=cmd,
            )
    tool_result = command_tool_result(result)
    return replace(
        tool_result,
        metadata={
            **tool_result.metadata,
            **sandbox_audit_metadata_from_tool_context(context),
        },
    )


__all__ = ["exec_command"]
