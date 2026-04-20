"""Block OS process wrappers in coordinated CodeAct Python mode."""

from __future__ import annotations

import ast

from pydantic import BaseModel

from tools.core.base import ToolExecutionContext
from tools.core.hooks import PreHookOutcome, ToolHookRegistry, default_registry
from tools.daytona_toolkit.hooks._common import is_coordinated_team_agent
from tools.daytona_toolkit.hooks.prehook._codeact_common import python_code

OS_PROCESS_POLICY_MESSAGE = (
    "CodeAct policy error: coordinated team lanes must use "
    "`daytona_codeact(command=\"...\")` shell mode or `shell(\"...\")` "
    "inside Python mode for repo commands. "
    "Replace `os.system()`/`os.popen()` wrappers."
)


def _python_os_process_policy_error(code: str) -> str | None:
    try:
        tree = ast.parse(code or "")
    except SyntaxError:
        return None

    os_aliases = {"os"}
    imported_process_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "os":
                    os_aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "os":
            for alias in node.names:
                if alias.name in {"system", "popen"}:
                    imported_process_names.add(alias.asname or alias.name)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id in os_aliases
            and func.attr in {"system", "popen"}
        ):
            return OS_PROCESS_POLICY_MESSAGE
        if isinstance(func, ast.Name) and func.id in imported_process_names:
            return OS_PROCESS_POLICY_MESSAGE
    return None


async def hook(
    tool_name: str,
    args: BaseModel,
    context: ToolExecutionContext,
) -> PreHookOutcome:
    if not is_coordinated_team_agent(context):
        return PreHookOutcome()
    code = python_code(args)
    if code is None:
        return PreHookOutcome()
    err = _python_os_process_policy_error(code)
    if err is not None:
        return PreHookOutcome(has_error=True, error_message=err)
    return PreHookOutcome()


def register(registry: ToolHookRegistry | None = None) -> None:
    reg = registry or default_registry()
    reg.register(
        "daytona_codeact",
        "pre",
        25,
        hook,
        name="daytona_codeact:python_process_policy",
    )
