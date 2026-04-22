"""Block destructive git commands in CodeAct shell mode."""

from __future__ import annotations

import re
import shlex

from pydantic import BaseModel

from tools.core.base import ToolExecutionContext
from tools.core.hooks import PreHookOutcome, ToolHookRegistry, default_registry
from tools.daytona_toolkit.hooks.prehook._codeact_common import shell_command

_DESTRUCTIVE_GIT_PATTERN = re.compile(
    r"git\s+(stash|reset\s+--hard|checkout\s+--\s|checkout\s+\.\s*$)",
    flags=re.IGNORECASE,
)
_GIT_CLEAN_PATTERN = re.compile(
    r"(?:^|[;&|]\s*)git\s+clean\b(?P<args>[^;&|]*)",
    flags=re.IGNORECASE,
)
_DESTRUCTIVE_GIT_MESSAGE = (
    "BLOCKED: destructive git commands (stash, reset --hard, checkout --, clean) "
    "are forbidden. They destroy other agents' work and bypass process audit. "
    "Use targeted edit tools instead."
)


def _clean_args_are_dry_run(args: list[str]) -> bool:
    for arg in args:
        if arg == "--":
            break
        if arg == "--dry-run":
            return True
        if arg.startswith("--"):
            continue
        if arg.startswith("-") and "n" in arg[1:]:
            return True
    return False


def _has_destructive_git_clean(command: str) -> bool:
    for match in _GIT_CLEAN_PATTERN.finditer(command or ""):
        raw_args = match.group("args") or ""
        try:
            args = shlex.split(raw_args)
        except ValueError:
            args = raw_args.split()
        if not _clean_args_are_dry_run(args):
            return True
    return False


def destructive_git_command_error(command: str) -> str | None:
    if _DESTRUCTIVE_GIT_PATTERN.search(command or "") or _has_destructive_git_clean(command):
        return _DESTRUCTIVE_GIT_MESSAGE
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
