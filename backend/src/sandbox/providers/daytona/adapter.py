"""Daytona implementation of the provider adapter seam."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from sandbox.providers.protocol import ProviderAdapter

if TYPE_CHECKING:
    from sandbox.api.models import RawExecResult


class DaytonaProviderAdapter:
    """Provider adapter that delegates exec to today's Daytona transport."""

    name: ClassVar[str] = "daytona"

    def __init__(self, *, transport: ProviderAdapter | None = None) -> None:
        if transport is None:
            from sandbox.daytona.transport import DaytonaTransport

            transport = DaytonaTransport()
        self._transport = transport

    async def exec(
        self,
        sandbox_id: str,
        command: str,
        *,
        cwd: str | None = None,
        timeout: int | None = None,
    ) -> "RawExecResult":
        return await self._transport.exec(
            sandbox_id,
            command,
            cwd=cwd,
            timeout=timeout,
        )


__all__ = ["DaytonaProviderAdapter"]
