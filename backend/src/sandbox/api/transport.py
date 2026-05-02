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
    SearchMatch,
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

    async def search(
        self,
        sandbox_id: str,
        pattern: str,
        *,
        root: str | None = None,
        include: str | None = None,
    ) -> Sequence[SearchMatch]: ...

    async def list_paths(
        self,
        sandbox_id: str,
        glob: str,
        *,
        root: str | None = None,
    ) -> Sequence[str]: ...

    async def ci_rpc(
        self,
        sandbox_id: str,
        payload: bytes,
        *,
        socket_path: str,
        timeout: int | None = None,
    ) -> bytes:
        """Round-trip a length-prefixed msgpack frame through the in-sandbox CI daemon.

        Implementations bridge ``payload`` bytes-for-bytes to the daemon's Unix
        socket at ``socket_path`` inside the sandbox and return the response
        frame unchanged. The bridge MUST be binary-safe across every byte 0-255
        (use base64 in transit if the underlying channel is text-only).

        Implementations that cannot support the verb raise
        :class:`NotImplementedError`; ``CiRpcClient`` falls back to the
        orchestrator-side python shim transparently.

        Raises ``ConnectionRefusedError`` (or a subclass-compatible
        ``OSError``) when the socket is unreachable so the caller's
        ``ensure_daemon`` retry path engages identically to the shim.
        """
        raise NotImplementedError("ci_rpc is not supported by this transport")


__all__ = ["SandboxTransport"]
