"""Overlay-shell invoker: drives one worker run per leased snapshot.

`OverlayRuntimeInvoker.invoke_sync` is the production seam; the snapshot
runner drives it via the async executor.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from sandbox.execution.overlay_request import OverlayShellRequest
from sandbox.execution.overlay_result import OverlayCapture
from sandbox.execution.overlay_worker import execute_request
from sandbox.layer_stack.manifest import Manifest


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

    def invoke_sync(
        self, *, request: OverlayShellRequest, manifest: Manifest
    ) -> OverlayCapture:
        return execute_request(
            request=request,
            manifest=manifest,
            storage_root=self.storage_root,
            run_dir=self._run_dir(request),
        )

    def _run_dir(self, request: OverlayShellRequest) -> Path:
        safe_id = "".join(
            char if char.isalnum() or char in ("-", "_") else "-"
            for char in request.request_id
        ).strip("-")
        return self.runtime_root / f"{safe_id or 'request'}-{uuid4().hex[:8]}"


__all__ = ["OverlayRuntimeInvoker"]
