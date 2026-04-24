"""Daytona post-hook registration."""

from __future__ import annotations

from tools.core.hooks import ToolHookRegistry
from tools.daytona_toolkit.hooks.posthook import (
    audited_write_policy,
    move_extend_scope,
    write_extend_scope,
)

_MODULES = (
    audited_write_policy,
    move_extend_scope,
    write_extend_scope,
)


def register_all(registry: ToolHookRegistry | None = None) -> None:
    for module in _MODULES:
        module.register(registry)
