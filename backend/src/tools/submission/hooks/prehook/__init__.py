"""Submission pre-hook registration."""

from __future__ import annotations

from tools.core.hooks import ToolHookRegistry
from tools.submission.hooks.prehook import scope_path_policy

_MODULES = (scope_path_policy,)


def register_all(registry: ToolHookRegistry | None = None) -> None:
    for module in _MODULES:
        module.register(registry)
