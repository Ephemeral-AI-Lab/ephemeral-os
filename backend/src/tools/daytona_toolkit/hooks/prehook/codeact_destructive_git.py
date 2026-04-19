"""Block destructive git commands in CodeAct shell mode."""

from __future__ import annotations

import re

from pydantic import BaseModel

from tools.core.base import ToolExecutionContext
from tools.core.hooks import PreHookOutcome, ToolHookRegistry, default_registry
from tools.daytona_toolkit.hooks.prehook._codeact_common import shell_command

_DESTRUCTIVE_GIT_PATTERN = re.compile(
    r"git\s+(stash|reset\s+--hard|checkout\s+--\s|checkout\s+\.\s*$|clean\s+-[fd])",
    flags=re.IGNORECASE,
)


def destructive_git_command_error(command: str) -> str | None:
    if _DESTRUCTIVE_GIT_PATTERN.search(command or ""):
        return (
            "BLOCKED: destructive git commands (stash, reset --hard, checkout --, clean) "
            "are forbidden. They destroy other agents' work and bypass process audit. "
            "Use targeted edit tools instead."
        )
    return None


async def hook(
    tool_name: str,
    args: BaseModel,
    context: ToolExecutionContext,
) -> PreHookOutcome:
    del context
    command = shell_command(args)
    if command is None:
        return PreHookOutcome()
    err = destructive_git_command_error(command)
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
