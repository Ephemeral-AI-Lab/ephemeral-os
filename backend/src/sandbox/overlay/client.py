"""Host-side client for overlay runtime operations."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from sandbox.overlay.runner.snapshot_overlay_runner import (
    OverlayShellRequest,
    SnapshotOverlayRunner,
)
from sandbox.runtime.overlay_shell.result_envelope import RuntimeResultEnvelope


_CLIENTS: dict[str, "OverlayClient"] = {}


def register_overlay_client(sandbox_id: str, client: "OverlayClient") -> None:
    """Bind a sandbox id to its typed overlay runtime client."""
    key = str(sandbox_id).strip()
    if not key:
        raise ValueError("sandbox_id must not be empty")
    _CLIENTS[key] = client


def dispose_overlay_client(sandbox_id: str) -> None:
    """Remove a typed overlay runtime client binding."""
    _CLIENTS.pop(str(sandbox_id), None)


def get_overlay_client(sandbox_id: str) -> "OverlayClient":
    """Return the typed overlay client bound to *sandbox_id*."""
    try:
        return _CLIENTS[str(sandbox_id)]
    except KeyError as exc:
        raise OverlayClientError(
            "MissingOverlayClient",
            f"no typed overlay client is registered for sandbox {sandbox_id!r}",
        ) from exc


class OverlayClientError(RuntimeError):
    """Raised when the typed overlay client is not bound to a runner."""

    def __init__(
        self,
        kind: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{kind}: {message}")
        self.kind = kind
        self.message = message
        self.details = details or {}


class OverlayClient:
    """Typed host route for overlay requests through ``runtime/server.py``."""

    def __init__(
        self,
        sandbox_id: str | None = None,
        *,
        runner: SnapshotOverlayRunner | None = None,
    ) -> None:
        if runner is None and sandbox_id is not None:
            runner = get_overlay_client(sandbox_id)._runner
        if runner is None:
            raise OverlayClientError(
                "MissingOverlayRunner",
                "OverlayClient requires a snapshot runner or registered sandbox binding",
            )
        self._runner = runner

    @property
    def runner(self) -> SnapshotOverlayRunner:
        return self._runner

    async def run(
        self,
        command: tuple[str, ...],
        *,
        request_id: str | None = None,
        cwd: str = ".",
        env: Mapping[str, str] | None = None,
        timeout_seconds: float | None = None,
    ) -> RuntimeResultEnvelope:
        return await self.shell(
            command,
            request_id=request_id,
            cwd=cwd,
            env=env,
            timeout_seconds=timeout_seconds,
        )

    async def shell(
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


__all__ = [
    "OverlayClient",
    "OverlayClientError",
    "dispose_overlay_client",
    "get_overlay_client",
    "register_overlay_client",
]
