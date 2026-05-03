"""Provider-neutral transport primitives consumed by SandboxApi and CI internals.

A single ``SandboxTransport`` implementation owns all coupling to a sandbox
provider's SDK (Daytona today, Modal/Docker/etc. tomorrow). Callers above
this layer never reference provider modules directly.

This is the raw layer: no audit, no attribution, no policy. Audit-bearing
flows live in ``SandboxApi``; CI internals consume ``SandboxTransport``
directly because they are the *producer* of audit signals — wrapping
their own writes in audit would be a self-loop.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from sandbox.api.models import (
    CheckedWriteResult,
    CheckedWriteSpec,
    RawExecResult,
)


class SandboxTransport(Protocol):
    """Raw provider-neutral primitives. All async."""

    name: str

    async def exec(
        self,
        sandbox_id: str,
        command: str,
        *,
        cwd: str | None = None,
        timeout: int | None = None,
    ) -> RawExecResult: ...

    async def read_bytes(self, sandbox_id: str, path: str) -> bytes: ...

    async def read_bytes_batch(
        self,
        sandbox_id: str,
        paths: Sequence[str],
    ) -> dict[str, bytes | None]: ...

    async def write_bytes(
        self,
        sandbox_id: str,
        path: str,
        content: bytes,
    ) -> None: ...

    async def apply_diff_batch_checked(
        self,
        sandbox_id: str,
        specs: Sequence[CheckedWriteSpec],
    ) -> CheckedWriteResult: ...


__all__ = ["SandboxTransport"]
