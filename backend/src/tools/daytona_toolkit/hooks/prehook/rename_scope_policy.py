"""Pre-commit scope policy for semantic Daytona renames."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from tools.core.base import ToolExecutionContext
from tools.core.ci_runtime import get_ci_service
from tools.core.hooks import PreHookOutcome, ToolHookRegistry, default_registry
from tools.daytona_toolkit.hooks._common import (
    _scope_deny_message,
    _team_repo_scope_deny_errors,
    _team_repo_write_error,
)

_CACHE_KEY = "_daytona_rename_preplan"


def _request_key(
    *,
    svc: Any,
    symbol: str,
    new_name: str,
    kind: Any,
    file_hint: str | None,
) -> tuple[int, str, str, str, str]:
    return (
        id(svc),
        symbol,
        new_name,
        str(kind or ""),
        str(file_hint or ""),
    )


async def hook(
    tool_name: str,
    args: BaseModel,
    context: ToolExecutionContext,
) -> PreHookOutcome:
    del tool_name
    svc = get_ci_service(context)
    if svc is None or not hasattr(svc, "rename_symbol_plan"):
        return PreHookOutcome()

    symbol = getattr(args, "symbol", None)
    new_name = getattr(args, "new_name", None)
    if not isinstance(symbol, str) or not isinstance(new_name, str):
        return PreHookOutcome()
    kind = getattr(args, "kind", None)
    file_hint = getattr(args, "file_hint", None)
    if file_hint is not None and not isinstance(file_hint, str):
        file_hint = None

    from tools.daytona_toolkit import rename_tool

    if rename_tool._validate_new_name(new_name) is not None:
        return PreHookOutcome()

    matches = rename_tool._resolve_symbol(
        svc,
        symbol=symbol,
        kind=kind,
        file_hint=file_hint,
    )
    if len(matches) != 1:
        return PreHookOutcome()

    sym = matches[0]
    resolved_path = rename_tool.resolve_daytona_path(
        str(getattr(sym, "file_path", "")),
        context,
    )
    pivot_line = int(getattr(sym, "line", 0) or 0)
    pivot_char = rename_tool._symbol_name_column(sym)
    try:
        plan = await rename_tool._rename_plan_batcher_for(svc).submit(
            file_path=resolved_path,
            line=pivot_line,
            character=pivot_char,
            new_name=new_name,
        )
    except Exception as exc:  # pragma: no cover - defensive
        return PreHookOutcome(
            has_error=True,
            error_message=f"LSP rename failed: {exc}",
        )

    changes = getattr(plan, "changes", ()) or ()
    if changes:
        test_file_errors: list[str] = []
        for change in changes:
            err = _team_repo_write_error(
                context,
                change.file_path,
                tool_name="daytona_rename_symbol",
            )
            if err is not None:
                test_file_errors.append(err)
        if test_file_errors:
            return PreHookOutcome(
                has_error=True,
                error_message=(
                    "Rename blocked by write-scope policy:\n  - "
                    + "\n  - ".join(test_file_errors)
                ),
            )

        scope_offenders = _team_repo_scope_deny_errors(
            context,
            [change.file_path for change in changes],
            tool_name="daytona_rename_symbol",
        )
        if scope_offenders:
            return PreHookOutcome(
                has_error=True,
                error_message=_scope_deny_message(
                    scope_offenders,
                    tool_name="daytona_rename_symbol",
                ),
            )

    context.metadata[_CACHE_KEY] = {
        "key": _request_key(
            svc=svc,
            symbol=symbol,
            new_name=new_name,
            kind=kind,
            file_hint=file_hint,
        ),
        "plan": plan,
        "resolved_path": resolved_path,
        "line": pivot_line,
        "character": pivot_char,
    }
    return PreHookOutcome()


def register(registry: ToolHookRegistry | None = None) -> None:
    reg = registry or default_registry()
    reg.register(
        "daytona_rename_symbol",
        "pre",
        15,
        hook,
        name="daytona_rename_symbol:scope_policy",
    )
