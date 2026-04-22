"""Block destructive shell commands in CodeAct shell mode."""

from __future__ import annotations

from pydantic import BaseModel

from tools.core.base import ToolExecutionContext
from tools.core.hooks import PreHookOutcome, ToolHookRegistry, default_registry
from tools.daytona_toolkit.ci_integration import destructive_shell_command_error
from tools.daytona_toolkit.hooks.prehook._codeact_common import codeact_shell_commands


async def hook(
    tool_name: str,
    args: BaseModel,
    context: ToolExecutionContext,
) -> PreHookOutcome:
    del context
    for command in codeact_shell_commands(args):
        err = destructive_shell_command_error(command)
        if err is not None:
            return PreHookOutcome(has_error=True, error_message=err)
    return PreHookOutcome()


def register(registry: ToolHookRegistry | None = None) -> None:
    reg = registry or default_registry()
    reg.register(
        "daytona_codeact",
        "pre",
        20,
        hook,
        name="daytona_codeact:destructive_shell",
    )
