"""Daytona toolkit package."""

from __future__ import annotations

from typing import Any

__all__ = ["DaytonaToolkit"]


def __getattr__(name: str) -> Any:
    if name == "DaytonaToolkit":
        from tools.daytona_toolkit.toolkit import DaytonaToolkit

        return DaytonaToolkit
    raise AttributeError(name)
