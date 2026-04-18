"""Daytona toolkit package."""

from __future__ import annotations

from typing import Any

# Side-effect import: registers Daytona platform hooks on the default hook
# registry whenever the toolkit package loads.
from tools.daytona_toolkit import hooks as _hooks  # noqa: F401

__all__ = ["DaytonaToolkit"]


def __getattr__(name: str) -> Any:
    if name == "DaytonaToolkit":
        from tools.daytona_toolkit.toolkit import DaytonaToolkit

        return DaytonaToolkit
    raise AttributeError(name)
