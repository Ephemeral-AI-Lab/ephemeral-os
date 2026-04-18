"""Normalize coordinated CodeAct shell commands before policy checks."""

from __future__ import annotations

from pydantic import BaseModel

from tools.core.base import ToolExecutionContext
from tools.core.hooks import PreHookOutcome, ToolHookRegistry, default_registry
from tools.daytona_toolkit._shell_policy import _normalize_team_shell_command
from tools.daytona_toolkit.hooks._common import _get_cwd, is_coordinated_team_agent
from tools.daytona_toolkit.hooks.prehook._codeact_common import shell_command


async def hook(
    tool_name: str,
    args: BaseModel,
    context: ToolExecutionContext,
) -> PreHookOutcome:
    command = shell_command(args)
    if command is None or not is_coordinated_team_agent(context):
        return PreHookOutcome()
    new_command, warnings = _normalize_team_shell_command(
        command,
        repo_root=_get_cwd(context),
    )
    if new_command == command and not warnings:
        return PreHookOutcome()
    return PreHookOutcome(
        tool_input=args.model_copy(update={"command": new_command}),
        advisories=tuple(warnings),
    )


def register(registry: ToolHookRegistry | None = None) -> None:
    reg = registry or default_registry()
    reg.register(
        "daytona_codeact",
        "pre",
        5,
        hook,
        name="daytona_codeact:shell_normalization",
    )
