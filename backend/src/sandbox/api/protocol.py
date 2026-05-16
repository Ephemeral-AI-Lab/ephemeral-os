"""Typed public sandbox API contracts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol


class SandboxTransport(Protocol):
    """Transport used by public workspace operations to call the sandbox runtime."""

    async def call(
        self,
        sandbox_id: str,
        op: str,
        payload: Mapping[str, object],
        *,
        timeout: int,
    ) -> dict[str, Any]: ...


__all__ = ["SandboxTransport"]
