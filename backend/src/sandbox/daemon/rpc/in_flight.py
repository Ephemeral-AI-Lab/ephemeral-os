"""Invocation-keyed daemon in-flight registry for cancellation and TTL cleanup."""

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
class InFlightInvocation:
    invocation_id: str
    task: asyncio.Task[object]
    agent_id: str
    op: str
    started_at: float
    last_seen: float
    background: bool = False
    engine_process_id: str = ""
    engine_started_at: float | None = None


class InFlightInvocationRegistry:
    """Tracks daemon-side asyncio tasks by invocation id."""

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
        self._by_invocation: dict[str, InFlightInvocation] = {}
        self._ttl_reaped_total = 0
        self._reaper_task: asyncio.Task[None] | None = None

    def register(
        self,
        invocation_id: str,
        task: asyncio.Task[object],
        *,
        agent_id: str = "",
        op: str = "",
        background: bool = False,
        engine_process_id: str = "",
        engine_started_at: float | None = None,
    ) -> None:
        if not invocation_id:
            return
        now = monotonic_now()
        self._by_invocation[invocation_id] = InFlightInvocation(
            invocation_id=invocation_id,
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

    def deregister(self, invocation_id: str) -> None:
        if invocation_id:
            self._by_invocation.pop(invocation_id, None)

    def cancel(self, invocation_id: str) -> bool:
        return self.cancel_task(invocation_id) is not None

    def cancel_task(self, invocation_id: str) -> asyncio.Task[object] | None:
        entry = self._by_invocation.get(invocation_id)
        if entry is None:
            return None
        entry.task.cancel()
        return entry.task

    def heartbeat(
        self,
        invocation_ids: list[str],
        *,
        engine_process_id: str = "",
        engine_started_at: float | None = None,
    ) -> int:
        now = monotonic_now()
        touched = 0
        for invocation_id in invocation_ids:
            entry = self._by_invocation.get(invocation_id)
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
            for entry in self._by_invocation.values()
            if entry.background and entry.agent_id == agent_id and not entry.task.done()
        )

    def metrics(self) -> dict[str, int]:
        return {
            "active_invocations": len(self._by_invocation),
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
            for entry in self._by_invocation.values()
            if entry.background and now - entry.last_seen >= self._ttl_seconds
        ]
        for entry in stale:
            logger.warning(
                "in-flight invocation %s op=%s agent_id=%s expired after %.0fs",
                entry.invocation_id,
                entry.op,
                entry.agent_id,
                now - entry.last_seen,
            )
            entry.task.cancel()
            self._by_invocation.pop(entry.invocation_id, None)
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


_REGISTRY: InFlightInvocationRegistry | None = None


def get_in_flight_registry() -> InFlightInvocationRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = InFlightInvocationRegistry()
    return _REGISTRY


def reset_in_flight_registry() -> InFlightInvocationRegistry:
    global _REGISTRY
    if _REGISTRY is not None:
        _REGISTRY.shutdown()
    _REGISTRY = InFlightInvocationRegistry()
    return _REGISTRY


__all__ = [
    "InFlightInvocation",
    "InFlightInvocationRegistry",
    "get_in_flight_registry",
    "reset_in_flight_registry",
]
