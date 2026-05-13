"""Sandbox tool package."""

from __future__ import annotations

from typing import Any


def __getattr__(name: str) -> Any:
    if name == "make_sandbox_tools":
        from tools.sandbox._lib.registry import make_sandbox_tools

        return make_sandbox_tools
    raise AttributeError(name)

__all__ = ["make_sandbox_tools"]
