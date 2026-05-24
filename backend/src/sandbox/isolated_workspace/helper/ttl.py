"""TTL eviction loop for isolated workspaces."""

from __future__ import annotations

import asyncio

from sandbox.isolated_workspace.helper.types import logger


class _IsolatedTtlMixin:
        async def _ttl_loop(self) -> None:
            """Background task started by ``initialize`` that runs periodic sweeps.

            Tick interval = ``max(0.5 s, min(ttl_s / 2, 30 s))`` so short TTLs
            (Tier 5's ``test_ttl_evict_and_audit`` sets ``TTL_S=1``) still see a
            sweep inside the test budget while the default 1800 s TTL stays at a
            modest 30 s heartbeat.
            """
            interval = max(0.5, min(self._config.ttl_s / 2.0, 30.0))
            while True:
                try:
                    await asyncio.sleep(interval)
                    await self.ttl_sweep()
                except asyncio.CancelledError:
                    return
                except Exception:  # pragma: no cover - background task
                    logger.exception("ttl_loop tick failed")

        async def ttl_sweep(self) -> int:
            now = self._clock()
            evicted = 0
            async with self._map_lock:
                stale = [
                    h for h in self._handles.values()
                    if now - h.last_activity > self._config.ttl_s
                    and h.active_calls == 0
                ]
            for handle in stale:
                try:
                    stats = await self.exit(handle.agent_id)
                    self._emit(
                        "sandbox_isolated_workspace_evicted",
                        {
                            "handle_id": handle.handle_id,
                            "reason": "ttl",
                            "lifetime_s": stats.get("lifetime_s", 0.0),
                            "upperdir_bytes_discarded": stats.get(
                                "evicted_upperdir_bytes", 0
                            ),
                            "total_ms": stats.get("total_ms", 0.0),
                            "phases_ms": stats.get("phases_ms", {}),
                        },
                    )
                    evicted += 1
                except Exception:  # pragma: no cover - logging only
                    logger.exception("ttl_sweep failed for %s", handle.handle_id)
            return evicted


__all__ = ["_IsolatedTtlMixin"]
