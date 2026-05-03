"""Legacy wide transport primitives consumed by SandboxApi and CI internals.

``ProviderAdapter`` is the new narrow provider seam for raw runtime/setup
execution. ``SandboxTransport`` remains as a deprecated superset while CI and
the current audit-aware API still need byte I/O and checked-write primitives.

This is the raw layer: no audit, no attribution, no policy. Audit-bearing
flows live in ``SandboxApi``; CI internals consume ``SandboxTransport``
directly because they are the *producer* of audit signals — wrapping
their own writes in audit would be a self-loop.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol

from sandbox.api.models import (
    CheckedWriteResult,
    CheckedWriteSpec,
    RawExecResult,
)

if TYPE_CHECKING:
    from sandbox.providers.protocol import ProviderAdapter
else:
    class ProviderAdapter(Protocol):
        """Runtime stub; the daemon bundle does not ship provider adapters."""

        name: str

        async def exec(
            self,
            sandbox_id: str,
            command: str,
            *,
            cwd: str | None = None,
            timeout: int | None = None,
        ) -> RawExecResult: ...


class SandboxTransport(ProviderAdapter, Protocol):
    """Deprecated legacy transport superset. All methods are async."""

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
