"""Task Center pre-hook registration."""

from __future__ import annotations

from tools.core.hooks import ToolHookRegistry

_MODULES: tuple = ()


def register_all(registry: ToolHookRegistry | None = None) -> None:
    for module in _MODULES:
        module.register(registry)
