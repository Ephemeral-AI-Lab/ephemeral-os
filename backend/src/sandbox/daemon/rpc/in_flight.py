"""Request-keyed daemon in-flight registry for cancellation and TTL cleanup."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass

from sandbox._shared.clock import monotonic_now

logger = logging.getLogger("sandbox.daemon.rpc.in_flight")

_DEFAULT_TTL_SECONDS = 300.0
_DEFAULT_REAPER_INTERVAL_S = 30.0
_ENV_TTL_S = "EOS_INFLIGHT_TTL_S"
_ENV_REAPER_INTERVAL_S = "EOS_INFLIGHT_REAPER_INTERVAL_S"


@dataclass
class InFlightRequest:
    request_id: str
    task: asyncio.Task[object]
    agent_id: str
    op: str
    started_at: float
    last_seen: float
    background: bool = False
    engine_process_id: str = ""
    engine_started_at: float | None = None


class InFlightRequestRegistry:
    """Tracks daemon-side asyncio tasks by request id."""

    def __init__(
        self,
        *,
        ttl_seconds: float | None = None,
        reaper_interval_s: float | None = None,
    ) -> None:
        self._ttl_seconds = (
            _env_float(_ENV_TTL_S, _DEFAULT_TTL_SECONDS)
            if ttl_seconds is None
            else float(ttl_seconds)
        )
        self._reaper_interval_s = (
            _env_float(_ENV_REAPER_INTERVAL_S, _DEFAULT_REAPER_INTERVAL_S)
            if reaper_interval_s is None
            else float(reaper_interval_s)
        )
        self._by_request: dict[str, InFlightRequest] = {}
        self._ttl_reaped_total = 0
        self._reaper_task: asyncio.Task[None] | None = None

    def register(
        self,
        request_id: str,
        task: asyncio.Task[object],
        *,
        agent_id: str = "",
        op: str = "",
        background: bool = False,
        engine_process_id: str = "",
        engine_started_at: float | None = None,
    ) -> None:
        if not request_id:
            return
        now = monotonic_now()
        self._by_request[request_id] = InFlightRequest(
            request_id=request_id,
            task=task,
            agent_id=agent_id,
            op=op,
            started_at=now,
            last_seen=now,
            background=background,
            engine_process_id=engine_process_id,
            engine_started_at=engine_started_at,
        )
        self._ensure_reaper_started()

    def deregister(self, request_id: str) -> None:
        if request_id:
            self._by_request.pop(request_id, None)

    def cancel(self, request_id: str) -> bool:
        return self.cancel_task(request_id) is not None

    def cancel_task(self, request_id: str) -> asyncio.Task[object] | None:
        entry = self._by_request.get(request_id)
        if entry is None:
            return None
        entry.task.cancel()
        return entry.task

    def heartbeat(
        self,
        request_ids: list[str],
        *,
        engine_process_id: str = "",
        engine_started_at: float | None = None,
    ) -> int:
        now = monotonic_now()
        touched = 0
        for request_id in request_ids:
            entry = self._by_request.get(request_id)
            if entry is None:
                continue
            entry.last_seen = now
            if engine_process_id:
                entry.engine_process_id = engine_process_id
            if engine_started_at is not None:
                entry.engine_started_at = engine_started_at
            touched += 1
        return touched

    def count_by_agent(self, agent_id: str) -> int:
        return sum(
            1
            for entry in self._by_request.values()
            if entry.background and entry.agent_id == agent_id and not entry.task.done()
        )

    def metrics(self) -> dict[str, int]:
        return {
            "active_requests": len(self._by_request),
            "ttl_reaped_total": self._ttl_reaped_total,
        }

    async def ttl_reaper_loop(self) -> None:
        while True:
            await asyncio.sleep(self._reaper_interval_s)
            self.reap_stale()

    def reap_stale(self) -> None:
        now = monotonic_now()
        stale = [
            entry
            for entry in self._by_request.values()
            if entry.background and now - entry.last_seen >= self._ttl_seconds
        ]
        for entry in stale:
            logger.warning(
                "in-flight request %s op=%s agent_id=%s expired after %.0fs",
                entry.request_id,
                entry.op,
                entry.agent_id,
                now - entry.last_seen,
            )
            entry.task.cancel()
            self._by_request.pop(entry.request_id, None)
            self._ttl_reaped_total += 1

    def shutdown(self) -> None:
        if self._reaper_task is not None:
            self._reaper_task.cancel()
            self._reaper_task = None

    def _ensure_reaper_started(self) -> None:
        if self._reaper_task is not None and not self._reaper_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._reaper_task = loop.create_task(self.ttl_reaper_loop())


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


_REGISTRY: InFlightRequestRegistry | None = None


def get_in_flight_registry() -> InFlightRequestRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = InFlightRequestRegistry()
    return _REGISTRY


def reset_in_flight_registry() -> InFlightRequestRegistry:
    global _REGISTRY
    if _REGISTRY is not None:
        _REGISTRY.shutdown()
    _REGISTRY = InFlightRequestRegistry()
    return _REGISTRY


__all__ = [
    "InFlightRequest",
    "InFlightRequestRegistry",
    "get_in_flight_registry",
    "reset_in_flight_registry",
]
