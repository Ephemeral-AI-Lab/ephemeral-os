"""Block CodeAct commands that route output through pipes, redirects, or repo-root cd."""

from __future__ import annotations

import ast
import re

from pydantic import BaseModel

from tools.core.base import ToolExecutionContext
from tools.core.hooks import PreHookOutcome, ToolHookRegistry, default_registry
from tools.daytona_toolkit.hooks.prehook._codeact_common import python_code, shell_command

PIPELINE_POLICY_MESSAGE = (
    "CodeAct policy error: commands must not contain `|`, `>`, `2>&1`, "
    "or a leading `cd /testbed &&` / `cd /workspace &&`. "
    "`daytona_codeact` already captures stdout/stderr and starts at the repo root. "
    "Use pytest flags (`-x`, `-k`, `--tb=short`), a narrower node id, "
    "or background execution to limit output."
)

_LEADING_CD_RE = re.compile(r"^\s*cd\s+/(testbed|workspace)(\s|/|$)")


def _first_offense(command: str) -> str | None:
    if _LEADING_CD_RE.match(command or ""):
        return PIPELINE_POLICY_MESSAGE

    quote: str | None = None
    escaped = False
    i = 0
    n = len(command or "")
    while i < n:
        char = command[i]
        if escaped:
            escaped = False
            i += 1
            continue
        if char == "\\":
            escaped = True
            i += 1
            continue
        if quote:
            if char == quote:
                quote = None
            i += 1
            continue
        if char in {"'", '"'}:
            quote = char
            i += 1
            continue
        if char == "|":
            return PIPELINE_POLICY_MESSAGE
        if char == ">":
            return PIPELINE_POLICY_MESSAGE
        if char == "2" and command[i : i + 4] == "2>&1":
            return PIPELINE_POLICY_MESSAGE
        i += 1
    return None


def shell_pipeline_policy_error(command: str) -> str | None:
    return _first_offense(command or "")


def python_pipeline_policy_error(code: str) -> str | None:
    try:
        tree = ast.parse(code or "")
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name) or node.func.id != "shell":
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant):
            continue
        command = node.args[0].value
        if isinstance(command, str):
            err = _first_offense(command)
            if err is not None:
                return err
    return None


async def hook(
    tool_name: str,
    args: BaseModel,
    context: ToolExecutionContext,
) -> PreHookOutcome:
    del context
    command = shell_command(args)
    if command is not None:
        err = shell_pipeline_policy_error(command)
    else:
        code = python_code(args)
        err = None if code is None else python_pipeline_policy_error(code)
    if err is not None:
        return PreHookOutcome(has_error=True, error_message=err)
    return PreHookOutcome()


def register(registry: ToolHookRegistry | None = None) -> None:
    reg = registry or default_registry()
    reg.register(
        "daytona_codeact",
        "pre",
        27,
        hook,
        name="daytona_codeact:output_pipeline_policy",
    )
