"""Block CodeAct file-edit side channels in coordinated team lanes."""

from __future__ import annotations

from pydantic import BaseModel

from tools.core.base import ToolExecutionContext
from tools.core.hooks import PreHookOutcome, ToolHookRegistry, default_registry
from tools.daytona_toolkit.hooks.prehook._codeact_common import python_code, shell_command


async def hook(
    tool_name: str,
    args: BaseModel,
    context: ToolExecutionContext,
) -> PreHookOutcome:
    from tools.daytona_toolkit.codeact_tool import (
        _enforce_codeact_file_edit_policy,
        _python_file_edit_policy_error,
        _shell_file_edit_policy_error,
    )

    if not _enforce_codeact_file_edit_policy(context):
        return PreHookOutcome()
    command = shell_command(args)
    if command is not None:
        err = _shell_file_edit_policy_error(command)
        if err is not None:
            return PreHookOutcome(has_error=True, error_message=err)
        return PreHookOutcome()
    code = python_code(args)
    if code is not None:
        err = _python_file_edit_policy_error(code)
        if err is not None:
            return PreHookOutcome(has_error=True, error_message=err)
    return PreHookOutcome()


def register(registry: ToolHookRegistry | None = None) -> None:
    reg = registry or default_registry()
    reg.register(
        "daytona_codeact",
        "pre",
        30,
        hook,
        name="daytona_codeact:file_edit_policy",
    )
