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
    ) -> dict[str, Any]:
        """Call one sandbox RPC.

        Implementations put a wire-level ``invocation_id`` on the daemon envelope.
        If ``payload`` already has ``invocation_id``, the same id is used for
        correlation between engine background tasks and daemon in-flight state.
        """
        ...


__all__ = ["SandboxTransport"]
