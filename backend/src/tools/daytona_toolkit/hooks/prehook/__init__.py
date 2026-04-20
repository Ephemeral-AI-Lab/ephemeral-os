"""Daytona pre-hook registration."""

from __future__ import annotations

from tools.core.hooks import ToolHookRegistry
from tools.daytona_toolkit.hooks.prehook import (
    codeact_destructive_git,
    codeact_destructive_shell,
    codeact_file_edit_policy,
    codeact_python_process_policy,
    codeact_stderr_suppression_policy,
    move_dst_scope_advisory,
    move_src_hard_block,
    move_src_scope_deny,
    repo_operation_guard,
    rename_scope_policy,
    write_scope_advisory,
    write_scope_deny,
    write_scope_hard_block,
)

_MODULES = (
    repo_operation_guard,
    write_scope_hard_block,
    write_scope_advisory,
    write_scope_deny,
    move_src_hard_block,
    move_src_scope_deny,
    move_dst_scope_advisory,
    codeact_destructive_git,
    codeact_destructive_shell,
    codeact_python_process_policy,
    codeact_stderr_suppression_policy,
    codeact_file_edit_policy,
    rename_scope_policy,
)


def register_all(registry: ToolHookRegistry | None = None) -> None:
    for module in _MODULES:
        module.register(registry)
