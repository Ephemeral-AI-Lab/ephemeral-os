"""Host-side client for overlay runtime operations."""

from __future__ import annotations

import uuid
from collections.abc import Mapping

from sandbox.overlay.runner.snapshot_overlay_runner import (
    OverlayShellRequest,
    SnapshotOverlayRunner,
)
from sandbox.runtime.overlay_shell.result_envelope import RuntimeResultEnvelope


class OverlayClient:
    """Typed host route for overlay requests through ``runtime/server.py``."""

    def __init__(
        self,
        *,
        runner: SnapshotOverlayRunner,
    ) -> None:
        self._runner = runner

    async def run(
        self,
        command: tuple[str, ...],
        *,
        request_id: str | None = None,
        cwd: str = ".",
        env: Mapping[str, str] | None = None,
        timeout_seconds: float | None = None,
    ) -> RuntimeResultEnvelope:
        return await self._runner.shell(
            OverlayShellRequest(
                request_id=request_id or uuid.uuid4().hex,
                command=command,
                cwd=cwd,
                env=env or {},
                timeout_seconds=timeout_seconds,
            )
        )


__all__ = ["OverlayClient"]
