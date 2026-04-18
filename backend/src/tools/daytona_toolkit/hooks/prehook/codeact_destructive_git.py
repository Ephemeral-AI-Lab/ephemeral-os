"""Block destructive git commands in CodeAct shell mode."""

from __future__ import annotations

from pydantic import BaseModel

from tools.core.base import ToolExecutionContext
from tools.core.hooks import PreHookOutcome, ToolHookRegistry, default_registry
from tools.daytona_toolkit.hooks.prehook._codeact_common import shell_command


async def hook(
    tool_name: str,
    args: BaseModel,
    context: ToolExecutionContext,
) -> PreHookOutcome:
    del context
    command = shell_command(args)
    if command is None:
        return PreHookOutcome()
    from tools.daytona_toolkit.codeact_tool import _destructive_git_command_error

    err = _destructive_git_command_error(command)
    if err is not None:
        return PreHookOutcome(has_error=True, error_message=err)
    return PreHookOutcome()


def register(registry: ToolHookRegistry | None = None) -> None:
    reg = registry or default_registry()
    reg.register(
        "daytona_codeact",
        "pre",
        10,
        hook,
        name="daytona_codeact:destructive_git",
    )
