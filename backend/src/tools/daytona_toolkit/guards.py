"""Pre-phase tool guards for the Daytona mutation toolkit.

Policy matrix (registered in ``_SCOPE_PATH_ARG`` below):

    tool                    test-file   outside-scope
    daytona_write_file      Deny        Advisory
    daytona_edit_file       Deny        Advisory
    daytona_delete_file     Deny        Deny
    daytona_move_file (src) Deny        Deny
    daytona_move_file (dst) —           Advisory (skip if src in scope)

``daytona_rename_symbol`` keeps its scope loop inline because plan paths
surface only after ``svc.rename_symbol_plan``; it reuses the same
``_team_repo_scope_deny_errors`` helper.

For ``is_folder=True`` the guard gates on the folder path string only;
member-level checks run in the tool body after enumeration.
"""

from __future__ import annotations

from pydantic import BaseModel

from tools.core.base import ToolExecutionContext
from tools.core.guards import (
    Advisory,
    Allow,
    Deny,
    GuardOutcome,
    MutateArgs,
    ToolGuardRegistry,
    default_registry,
)
from tools.daytona_toolkit._daytona_utils import (
    _get_cwd,
    _resolve_path,
    _scope_deny_message,
    _team_repo_scope_deny_errors,
    _team_repo_write_error,
    _team_repo_write_warning,
    _write_scope_covers,
    is_coordinated_team_agent,
)
from tools.daytona_toolkit._shell_policy import _normalize_team_shell_command
from tools.daytona_toolkit.ci_integration import destructive_shell_command_error

# Per-tool primary-path arg: the field on ``args`` that the single-path
# write-scope guards check. Schemas drifted after initial registration
# (``DaytonaDeleteFileInput`` uses ``path``, ``DaytonaMoveFileInput`` uses
# ``src_path``), so explicit registration is safer than duck-typing.
# ``None`` → the tool isn't registered for these guards.
_SCOPE_PATH_ARG: dict[str, str] = {
    "daytona_write_file": "file_path",
    "daytona_edit_file": "file_path",
    "daytona_delete_file": "path",
    "daytona_move_file": "src_path",
}


def _str_arg(args: BaseModel, name: str) -> str | None:
    value = getattr(args, name, None)
    return value if isinstance(value, str) and value else None


def _scope_path(tool_name: str, args: BaseModel) -> str | None:
    field = _SCOPE_PATH_ARG.get(tool_name)
    return _str_arg(args, field) if field else None


def _scope_role(tool_name: str) -> str | None:
    """The ``role`` label used in Deny messages for multi-path tools.

    Single-path tools get ``None`` — the Deny message is unqualified.
    Move qualifies with ``src_path`` so the agent sees which side of the
    move tripped the policy.
    """
    return "src_path" if tool_name == "daytona_move_file" else None


async def write_scope_hard_block_guard(
    tool_name: str,
    args: BaseModel,
    context: ToolExecutionContext,
) -> GuardOutcome:
    """Deny test-file edits in coordinated team lanes.

    Wraps :func:`_team_repo_write_error`: the helper returns a human-readable
    error when the coordinated-team developer lane targets a test file
    without explicit authorization, otherwise ``None``.
    """
    path = _scope_path(tool_name, args)
    if path is None:
        return Allow()
    err = _team_repo_write_error(
        context, _resolve_path(path, context), tool_name=tool_name,
    )
    return Deny(message=err) if err is not None else Allow()


async def write_scope_advisory_guard(
    tool_name: str,
    args: BaseModel,
    context: ToolExecutionContext,
) -> GuardOutcome:
    """Emit an advisory for writes outside the configured write_scope.

    Wraps :func:`_team_repo_write_warning`: the helper records a
    coordination warning via :func:`record_coordination_warning` and returns
    the advisory string, escalating after 3+ outside-scope warnings.
    """
    path = _scope_path(tool_name, args)
    if path is None:
        return Allow()
    warn = _team_repo_write_warning(
        context, _resolve_path(path, context), tool_name=tool_name,
    )
    if warn is None:
        return Allow()
    return Advisory(warnings=(warn,), category="outside_write_scope")


async def write_scope_deny_guard(
    tool_name: str,
    args: BaseModel,
    context: ToolExecutionContext,
) -> GuardOutcome:
    """Deny writes to paths outside ``write_scope`` in coordinated lanes.

    For ``is_folder=True`` the guard only gates on the folder path string;
    the tool body enumerates descendants and calls
    :func:`_team_repo_scope_deny_errors` directly on the member list.
    """
    path = _scope_path(tool_name, args)
    if path is None:
        return Allow()
    offenders = _team_repo_scope_deny_errors(
        context, [_resolve_path(path, context)], tool_name=tool_name,
    )
    if not offenders:
        return Allow()
    return Deny(
        message=_scope_deny_message(
            offenders, tool_name=tool_name, role=_scope_role(tool_name),
        ),
    )


async def move_dst_scope_advisory_guard(
    tool_name: str,
    args: BaseModel,
    context: ToolExecutionContext,
) -> GuardOutcome:
    """Advisory on outside-scope dst; suppressed when src is in-scope.

    A move whose src is already owned is a naming op, not a widening op;
    ``_extend_write_scope`` in the tool body widens the scope to cover
    dst on success.
    """
    dst = _str_arg(args, "target_path")
    if dst is None:
        return Allow()
    src = _str_arg(args, "src_path")
    if src is not None and _write_scope_covers(context, _resolve_path(src, context)):
        return Allow()
    warn = _team_repo_write_warning(
        context, _resolve_path(dst, context), tool_name=tool_name,
    )
    if warn is None:
        return Allow()
    return Advisory(warnings=(warn,), category="outside_write_scope")


def register_write_scope_guards(registry: ToolGuardRegistry | None = None) -> None:
    """Register write-scope guards for every tool in ``_SCOPE_PATH_ARG``."""
    reg = registry or default_registry()
    for tool_name in _SCOPE_PATH_ARG:
        reg.register(
            tool_name, "pre", 10, write_scope_hard_block_guard,
            name=f"{tool_name}:write_scope_hard_block",
        )
        if tool_name in {"daytona_delete_file", "daytona_move_file"}:
            reg.register(
                tool_name, "pre", 15, write_scope_deny_guard,
                name=f"{tool_name}:write_scope_deny",
            )
        else:
            reg.register(
                tool_name, "pre", 20, write_scope_advisory_guard,
                name=f"{tool_name}:write_scope_advisory",
            )
    reg.register(
        "daytona_move_file", "pre", 20, move_dst_scope_advisory_guard,
        name="daytona_move_file:dst_scope_advisory",
    )


# ---------------------------------------------------------------------------
# CodeAct host-side guards
#
# Note: The in-sandbox wrapper in ``codeact_tool._WRAPPER_TEMPLATE`` /
# ``_shell_policy.shell_policy_source()`` re-emits the same destructive /
# file-edit patterns into the executing Python process. That second-line
# enforcement runs in a different Python process and cannot be replaced by
# host-side guards. These guards handle the *host-side* pre-flight only.
# ---------------------------------------------------------------------------


def _codeact_shell_command(args: BaseModel) -> str | None:
    """Return the effective shell ``command`` if this call is shell mode."""
    from tools.daytona_toolkit.codeact_tool import _resolve_mode

    resolved_mode, err = _resolve_mode(
        mode=getattr(args, "mode", None),
        code=getattr(args, "code", None),
        command=getattr(args, "command", None),
    )
    if err is not None or resolved_mode != "shell":
        return None
    return str(getattr(args, "command", "") or "")


def _codeact_python_code(args: BaseModel) -> str | None:
    """Return the effective python ``code`` if this call is python mode."""
    from tools.daytona_toolkit.codeact_tool import _resolve_mode

    resolved_mode, err = _resolve_mode(
        mode=getattr(args, "mode", None),
        code=getattr(args, "code", None),
        command=getattr(args, "command", None),
    )
    if err is not None or resolved_mode != "python":
        return None
    return str(getattr(args, "code", "") or "")


async def codeact_shell_normalization_guard(
    tool_name: str,
    args: BaseModel,
    context: ToolExecutionContext,
) -> GuardOutcome:
    """Strip ``cd <repo_root>`` prefix and stderr-redirection plumbing.

    Coordinated-team-agent only. Mirrors the historical inline call at
    ``codeact_tool.daytona_codeact`` so subsequent guards see the normalized
    command.
    """
    command = _codeact_shell_command(args)
    if command is None or not is_coordinated_team_agent(context):
        return Allow()
    new_command, warnings = _normalize_team_shell_command(
        command,
        repo_root=_get_cwd(context),
    )
    if new_command == command and not warnings:
        return Allow()
    new_args = args.model_copy(update={"command": new_command})
    return MutateArgs(new_args=new_args, warnings=tuple(warnings))


async def codeact_destructive_git_guard(
    tool_name: str,
    args: BaseModel,
    context: ToolExecutionContext,
) -> GuardOutcome:
    """Block ``git stash``, ``git reset --hard``, ``git checkout -- / .``,
    ``git clean -fd``."""
    from tools.daytona_toolkit.codeact_tool import _destructive_git_command_error

    command = _codeact_shell_command(args)
    if command is None:
        return Allow()
    err = _destructive_git_command_error(command)
    if err is not None:
        return Deny(message=err)
    return Allow()


async def codeact_destructive_shell_guard(
    tool_name: str,
    args: BaseModel,
    context: ToolExecutionContext,
) -> GuardOutcome:
    """Block recursive ``rm``/``mv``/``chmod``/``chown`` on workspace roots,
    ``mkfs``, ``dd of=/``."""
    command = _codeact_shell_command(args)
    if command is None:
        return Allow()
    err = destructive_shell_command_error(command)
    if err is not None:
        return Deny(message=err)
    return Allow()


async def codeact_file_edit_policy_guard(
    tool_name: str,
    args: BaseModel,
    context: ToolExecutionContext,
) -> GuardOutcome:
    """Block file-edit side channels in CodeAct (shell + python).

    Only fires when ``_enforce_codeact_file_edit_policy`` returns True
    (coordinated-team-agent with team context). Mirrors the host-side
    pre-flight blocks at ``daytona_codeact`` lines 1095-1102.
    """
    from tools.daytona_toolkit.codeact_tool import (
        _enforce_codeact_file_edit_policy,
        _python_file_edit_policy_error,
        _shell_file_edit_policy_error,
    )

    if not _enforce_codeact_file_edit_policy(context):
        return Allow()
    shell_command = _codeact_shell_command(args)
    if shell_command is not None:
        err = _shell_file_edit_policy_error(shell_command)
        if err is not None:
            return Deny(message=err)
        return Allow()
    python_code = _codeact_python_code(args)
    if python_code is not None:
        err = _python_file_edit_policy_error(python_code)
        if err is not None:
            return Deny(message=err)
    return Allow()


def register_codeact_guards(registry: ToolGuardRegistry | None = None) -> None:
    """Register the host-side CodeAct guards."""
    reg = registry or default_registry()
    reg.register(
        "daytona_codeact",
        "pre",
        5,
        codeact_shell_normalization_guard,
        name="daytona_codeact:shell_normalization",
    )
    reg.register(
        "daytona_codeact",
        "pre",
        10,
        codeact_destructive_git_guard,
        name="daytona_codeact:destructive_git",
    )
    reg.register(
        "daytona_codeact",
        "pre",
        20,
        codeact_destructive_shell_guard,
        name="daytona_codeact:destructive_shell",
    )
    reg.register(
        "daytona_codeact",
        "pre",
        30,
        codeact_file_edit_policy_guard,
        name="daytona_codeact:file_edit_policy",
    )


# Register eagerly so importing the toolkit activates the guards.
register_write_scope_guards()
register_codeact_guards()
