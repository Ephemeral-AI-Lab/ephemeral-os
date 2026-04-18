"""Register Daytona platform hooks."""

from __future__ import annotations

from tools.core.hooks import ToolHookRegistry
from tools.daytona_toolkit.hooks import posthook, prehook


def register_all(registry: ToolHookRegistry | None = None) -> None:
    prehook.register_all(registry)
    posthook.register_all(registry)


register_all()
