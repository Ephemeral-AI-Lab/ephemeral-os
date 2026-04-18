"""Daytona post-hook registration."""

from __future__ import annotations

from tools.core.hooks import ToolHookRegistry
from tools.daytona_toolkit.hooks.posthook import codeact_audited_write_policy

_MODULES = (codeact_audited_write_policy,)


def register_all(registry: ToolHookRegistry | None = None) -> None:
    for module in _MODULES:
        module.register(registry)
