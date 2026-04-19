"""Normalize coordinated CodeAct shell commands before policy checks."""

from __future__ import annotations

import ast

from pydantic import BaseModel

from tools.core.base import ToolExecutionContext
from tools.core.hooks import PreHookOutcome, ToolHookRegistry, default_registry
from tools.daytona_toolkit._shell_policy import _normalize_team_shell_command
from tools.daytona_toolkit.hooks._common import _get_cwd, is_coordinated_team_agent
from tools.daytona_toolkit.hooks.prehook._codeact_common import python_code, shell_command

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


class _PythonShellCallNormalizer(ast.NodeTransformer):
    def __init__(self, *, repo_root: str | None) -> None:
        self.repo_root = repo_root
        self.warnings: list[str] = []
        self.changed = False

    def visit_Call(self, node: ast.Call) -> ast.AST:
        self.generic_visit(node)
        if not (
            isinstance(node.func, ast.Name)
            and node.func.id == "shell"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            return node
        command = node.args[0].value
        new_command, warnings = _normalize_team_shell_command(
            command,
            repo_root=self.repo_root,
        )
        if new_command == command and not warnings:
            return node
        self.changed = True
        self.warnings.extend(warnings)
        node.args[0] = ast.copy_location(ast.Constant(new_command), node.args[0])
        return node


def _normalize_python_shell_calls(
    code: str,
    *,
    repo_root: str | None,
) -> tuple[str | None, list[str]]:
    try:
        tree = ast.parse(code or "")
    except SyntaxError:
        return None, []
    normalizer = _PythonShellCallNormalizer(repo_root=repo_root)
    updated = normalizer.visit(tree)
    if not normalizer.changed:
        return None, []
    ast.fix_missing_locations(updated)
    return ast.unparse(updated), normalizer.warnings


async def hook(
    tool_name: str,
    args: BaseModel,
    context: ToolExecutionContext,
) -> PreHookOutcome:
    if not is_coordinated_team_agent(context):
        return PreHookOutcome()
    command = shell_command(args)
    if command is None:
        code = python_code(args)
        if code is None:
            return PreHookOutcome()
        err = _python_os_process_policy_error(code)
        if err is not None:
            return PreHookOutcome(has_error=True, error_message=err)
        new_code, warnings = _normalize_python_shell_calls(
            code,
            repo_root=_get_cwd(context),
        )
        if new_code is None and not warnings:
            return PreHookOutcome()
        return PreHookOutcome(
            tool_input=args.model_copy(update={"code": new_code or code}),
            advisories=tuple(warnings),
        )
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
