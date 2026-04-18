"""Deny moves whose source is outside write scope."""

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
    path = resolved_arg(args, "src_path", context)
    if path is None:
        return PreHookOutcome()
    offenders = _team_repo_scope_deny_errors(context, [path], tool_name=tool_name)
    if not offenders:
        return PreHookOutcome()
    return PreHookOutcome(
        has_error=True,
        error_message=_scope_deny_message(
            offenders,
            tool_name=tool_name,
            role="src_path",
        ),
    )


def register(registry: ToolHookRegistry | None = None) -> None:
    reg = registry or default_registry()
    reg.register(
        "daytona_move_file",
        "pre",
        15,
        hook,
        name="daytona_move_file:src_scope_deny",
    )
