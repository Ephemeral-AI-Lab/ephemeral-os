"""Register submission platform hooks."""

from __future__ import annotations

from tools.core.hooks import ToolHookRegistry
from tools.submission.hooks import prehook


def register_all(registry: ToolHookRegistry | None = None) -> None:
    prehook.register_all(registry)


register_all()
