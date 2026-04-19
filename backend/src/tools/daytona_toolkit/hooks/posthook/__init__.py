"""Daytona post-hook registration."""

from __future__ import annotations

from tools.core.hooks import ToolHookRegistry
from tools.daytona_toolkit.hooks.posthook import (
    ambient_change_warning,
    audited_write_policy,
)

_MODULES = (audited_write_policy, ambient_change_warning)


def register_all(registry: ToolHookRegistry | None = None) -> None:
    for module in _MODULES:
        module.register(registry)
