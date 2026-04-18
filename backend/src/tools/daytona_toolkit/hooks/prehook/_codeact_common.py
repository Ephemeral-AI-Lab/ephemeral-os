"""Shared helpers for CodeAct pre-hooks."""

from __future__ import annotations

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
