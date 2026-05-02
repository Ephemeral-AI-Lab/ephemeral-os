"""Daytona-specific exec and recovery primitives."""

from __future__ import annotations

from typing import Any, Protocol


class _SandboxContext(Protocol):
    """Mapping-like runtime context used by sandbox and tool callers."""

    def get(self, key: str, default: Any = None) -> Any: ...

    def __setitem__(self, key: str, value: Any) -> None: ...


__all__ = ["_SandboxContext"]
