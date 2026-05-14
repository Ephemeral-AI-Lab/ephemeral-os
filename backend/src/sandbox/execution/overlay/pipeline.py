"""Overlay-shell invoker: drives one worker run per leased snapshot.

`OverlayRuntimeInvoker` runs `execute_request` (the worker) and stamps
invoker-side timings on the returned capture. `OverlayInvoker` is the
duck-typed seam tests substitute against.

The user-command stage (`run_user_command`, `OverlayCommandResult`) lives
in `.worker` so the worker module is self-contained and there is no
pipeline↔worker import cycle.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4

from sandbox.daemon.async_bridge import run_sync_in_executor
from sandbox.execution.overlay.request import OverlayShellRequest
from sandbox.execution.overlay.result import OverlayCapture
from sandbox.execution.overlay.worker import execute_request
from sandbox.layer_stack.manifest import Manifest
from sandbox.timing import monotonic_now


@runtime_checkable
class OverlayInvoker(Protocol):
    async def invoke(
        self, *, request: OverlayShellRequest, manifest: Manifest
    ) -> OverlayCapture: ...

    def invoke_sync(
        self, *, request: OverlayShellRequest, manifest: Manifest
    ) -> OverlayCapture: ...


class OverlayRuntimeInvoker:
    """Invoke the runtime-local overlay shell command and return its capture."""

    def __init__(
        self,
        *,
        storage_root: str | Path,
        runtime_root: str | Path | None = None,
    ) -> None:
        self.storage_root = Path(storage_root)
        self.runtime_root = (
            Path(runtime_root)
            if runtime_root is not None
            else self.storage_root / "runtime" / "overlay_shell"
        )

    async def invoke(
        self, *, request: OverlayShellRequest, manifest: Manifest
    ) -> OverlayCapture:
        return await run_sync_in_executor(
            self.invoke_sync, request=request, manifest=manifest
        )

    def invoke_sync(
        self, *, request: OverlayShellRequest, manifest: Manifest
    ) -> OverlayCapture:
        invoke_start = monotonic_now()
        capture, worker_start, worker_elapsed = _run_worker_with_timings(
            request_payload=request.to_dict(),
            manifest_payload=manifest.to_dict(),
            storage_root=self.storage_root,
            run_dir=self._run_dir(request),
        )
        invoke_elapsed = monotonic_now() - invoke_start
        queue_wait = max(0.0, worker_start - invoke_start)
        non_worker_elapsed = max(0.0, invoke_elapsed - worker_elapsed)
        return replace(
            capture,
            timings={
                **dict(capture.timings),
                "overlay.invoker.queue_wait_s": queue_wait,
                "overlay.invoker.worker_total_s": worker_elapsed,
                "overlay.invoker.resume_wait_s": max(0.0, non_worker_elapsed - queue_wait),
                "overlay.invoker.total_s": invoke_elapsed,
            },
        )

    def _run_dir(self, request: OverlayShellRequest) -> Path:
        safe_id = "".join(
            char if char.isalnum() or char in ("-", "_") else "-"
            for char in request.request_id
        ).strip("-")
        return self.runtime_root / f"{safe_id or 'request'}-{uuid4().hex[:8]}"


def _run_worker_with_timings(
    *,
    request_payload: Mapping[str, Any],
    manifest_payload: Mapping[str, Any],
    storage_root: Path,
    run_dir: Path,
) -> tuple[OverlayCapture, float, float]:
    worker_start = monotonic_now()
    capture = execute_request(
        request_payload=request_payload,
        manifest_payload=manifest_payload,
        storage_root=storage_root,
        run_dir=run_dir,
    )
    return capture, worker_start, monotonic_now() - worker_start


__all__ = [
    "OverlayInvoker",
    "OverlayRuntimeInvoker",
]
