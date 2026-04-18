"""Deny single-path deletes outside write scope."""

from __future__ import annotations

from pydantic import BaseModel

from tools.core.base import ToolExecutionContext
from tools.core.hooks import PreHookOutcome, ToolHookRegistry, default_registry
from tools.daytona_toolkit.hooks._common import (
    _scope_deny_message,
    _team_repo_scope_deny_errors,
    resolved_arg,
)


async def hook(
    tool_name: str,
    args: BaseModel,
    context: ToolExecutionContext,
) -> PreHookOutcome:
    path = resolved_arg(args, "path", context)
    if path is None:
        return PreHookOutcome()
    offenders = _team_repo_scope_deny_errors(context, [path], tool_name=tool_name)
    if not offenders:
        return PreHookOutcome()
    return PreHookOutcome(
        has_error=True,
        error_message=_scope_deny_message(offenders, tool_name=tool_name),
    )


def register(registry: ToolHookRegistry | None = None) -> None:
    reg = registry or default_registry()
    reg.register(
        "daytona_delete_file",
        "pre",
        15,
        hook,
        name="daytona_delete_file:write_scope_deny",
    )
