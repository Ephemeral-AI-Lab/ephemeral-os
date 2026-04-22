"""Shared helpers for CodeAct pre-hooks."""

from __future__ import annotations

import ast

from pydantic import BaseModel


def shell_command(args: BaseModel) -> str | None:
    from tools.daytona_toolkit.codeact_tool import _resolve_mode

    resolved_mode, err = _resolve_mode(
        mode=getattr(args, "mode", None),
        code=getattr(args, "code", None),
        command=getattr(args, "command", None),
    )
    if err is not None or resolved_mode != "shell":
        return None
    return str(getattr(args, "command", "") or "")


def python_code(args: BaseModel) -> str | None:
    from tools.daytona_toolkit.codeact_tool import _resolve_mode

    resolved_mode, err = _resolve_mode(
        mode=getattr(args, "mode", None),
        code=getattr(args, "code", None),
        command=getattr(args, "command", None),
    )
    if err is not None or resolved_mode != "python":
        return None
    return str(getattr(args, "code", "") or "")


def python_shell_commands(code: str) -> list[str]:
    try:
        tree = ast.parse(code or "")
    except SyntaxError:
        return []

    string_bindings: dict[str, str] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    string_bindings[target.id] = node.value.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            string_bindings[node.target.id] = node.value.value

    commands: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name) or node.func.id != "shell":
            continue
        if not node.args:
            continue
        arg = node.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            commands.append(arg.value)
        elif isinstance(arg, ast.Name):
            command = string_bindings.get(arg.id)
            if command is not None:
                commands.append(command)
    return commands
