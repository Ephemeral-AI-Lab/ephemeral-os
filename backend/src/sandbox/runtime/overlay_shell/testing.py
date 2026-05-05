"""Runtime invokers used by sandbox API test setups."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from sandbox.overlay.capture.types import OverlayCapture
from sandbox.overlay.runner.runtime_invoker import RuntimeInvoker
from sandbox.overlay.runner.snapshot_overlay_runner import OverlayShellRequest


class AsyncBarrier:
    """Async barrier for coordinating concurrent runtime invocations."""

    def __init__(self, parties: int) -> None:
        self._parties = max(1, int(parties))
        self._arrived = 0
        self._lock = asyncio.Lock()
        self._event = asyncio.Event()

    async def wait(self) -> None:
        async with self._lock:
            self._arrived += 1
            if self._arrived >= self._parties:
                self._event.set()
        await asyncio.wait_for(self._event.wait(), timeout=10)


class BarrierRuntimeInvoker:
    """Hold shell invocations until every caller has acquired its snapshot."""

    def __init__(self, *, storage_root: Path, parties: int) -> None:
        self._inner = RuntimeInvoker(storage_root=storage_root)
        self._barrier = AsyncBarrier(parties)

    async def invoke(
        self,
        *,
        request: OverlayShellRequest,
        manifest: Any,
    ) -> OverlayCapture:
        await self._barrier.wait()
        return await self._inner.invoke(request=request, manifest=manifest)


__all__ = ["AsyncBarrier", "BarrierRuntimeInvoker"]
