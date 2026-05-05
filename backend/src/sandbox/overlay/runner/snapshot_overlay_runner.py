"""Acquire a frozen layer-stack snapshot and run one overlay shell request."""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any
from typing import Protocol

from sandbox.layer_stack.manifest import Manifest
from sandbox.layer_stack.stack_manager import LayerStackManager
from sandbox.overlay.capture.types import OverlayCapture


@dataclass(frozen=True)
class OverlayShellRequest:
    """One per-call shell request against a leased layer-stack snapshot."""

    request_id: str
    command: tuple[str, ...]
    cwd: str
    env: Mapping[str, str]
    timeout_seconds: float | None

    def __post_init__(self) -> None:
        request_id = str(self.request_id).strip()
        if not request_id:
            raise ValueError("request_id must not be empty")
        command = tuple(str(part) for part in self.command)
        if not command or any(part == "" for part in command):
            raise ValueError("command must contain non-empty argv parts")
        timeout = self.timeout_seconds
        if timeout is not None and timeout <= 0:
            raise ValueError("timeout_seconds must be positive when provided")
        object.__setattr__(self, "request_id", request_id)
        object.__setattr__(self, "command", command)
        object.__setattr__(self, "cwd", str(self.cwd).strip() or ".")
        object.__setattr__(
            self,
            "env",
            {str(key): str(value) for key, value in self.env.items()},
        )


def overlay_shell_request_to_dict(request: OverlayShellRequest) -> dict[str, Any]:
    return {
        "request_id": request.request_id,
        "command": list(request.command),
        "cwd": request.cwd,
        "env": dict(request.env),
        "timeout_seconds": request.timeout_seconds,
    }


def overlay_shell_request_from_dict(payload: Mapping[str, Any]) -> OverlayShellRequest:
    command_raw = payload.get("command")
    if not isinstance(command_raw, list):
        raise ValueError("OverlayShellRequest.command must be a list")
    env_raw = payload.get("env") or {}
    if not isinstance(env_raw, Mapping):
        raise ValueError("OverlayShellRequest.env must be an object")
    timeout_raw = payload.get("timeout_seconds")
    return OverlayShellRequest(
        request_id=str(payload.get("request_id") or ""),
        command=tuple(str(part) for part in command_raw),
        cwd=str(payload.get("cwd") or "."),
        env={str(key): str(value) for key, value in env_raw.items()},
        timeout_seconds=float(timeout_raw) if timeout_raw is not None else None,
    )


class _RuntimeInvoker(Protocol):
    async def invoke(
        self,
        *,
        request: OverlayShellRequest,
        manifest: Manifest,
    ) -> OverlayCapture: ...


class _SyncRuntimeInvoker(Protocol):
    def invoke_sync(
        self,
        *,
        request: OverlayShellRequest,
        manifest: Manifest,
    ) -> OverlayCapture: ...


class SnapshotOverlayRunner:
    """Lease a snapshot, invoke runtime capture, and release the lease."""

    def __init__(
        self,
        layer_stack: LayerStackManager,
        *,
        invoker: _RuntimeInvoker | None = None,
    ) -> None:
        self._layer_stack = layer_stack
        if invoker is None:
            from sandbox.overlay.runner.runtime_invoker import RuntimeInvoker

            invoker = RuntimeInvoker(storage_root=layer_stack.storage_root)
        self._invoker = invoker

    async def shell(self, request: OverlayShellRequest) -> OverlayCapture:
        total_start = time.perf_counter()
        lease_start = time.perf_counter()
        lease = self._layer_stack.acquire_snapshot_lease(request.request_id)
        timings = {
            "overlay.lease_acquire_s": time.perf_counter() - lease_start,
        }
        try:
            invoke_start = time.perf_counter()
            capture = await self._invoker.invoke(
                request=request,
                manifest=lease.manifest,
            )
        finally:
            timings["overlay.invoke_total_s"] = time.perf_counter() - invoke_start
            release_start = time.perf_counter()
            self._layer_stack.release_lease(lease.lease_id)
            timings["overlay.lease_release_s"] = time.perf_counter() - release_start
            timings["overlay.runner_total_s"] = time.perf_counter() - total_start
        return replace(capture, timings={**capture.timings, **timings})

    @property
    def supports_sync(self) -> bool:
        return callable(getattr(self._invoker, "invoke_sync", None))

    def shell_sync(self, request: OverlayShellRequest) -> OverlayCapture:
        invoke_sync = getattr(self._invoker, "invoke_sync", None)
        if not callable(invoke_sync):
            raise RuntimeError("overlay runner invoker does not support sync shell")

        total_start = time.perf_counter()
        lease_start = time.perf_counter()
        lease = self._layer_stack.acquire_snapshot_lease(request.request_id)
        timings = {
            "overlay.lease_acquire_s": time.perf_counter() - lease_start,
        }
        try:
            invoke_start = time.perf_counter()
            capture = invoke_sync(request=request, manifest=lease.manifest)
        finally:
            timings["overlay.invoke_total_s"] = time.perf_counter() - invoke_start
            release_start = time.perf_counter()
            self._layer_stack.release_lease(lease.lease_id)
            timings["overlay.lease_release_s"] = time.perf_counter() - release_start
            timings["overlay.runner_total_s"] = time.perf_counter() - total_start
        return replace(capture, timings={**capture.timings, **timings})


__all__ = [
    "OverlayShellRequest",
    "SnapshotOverlayRunner",
    "overlay_shell_request_from_dict",
    "overlay_shell_request_to_dict",
]
