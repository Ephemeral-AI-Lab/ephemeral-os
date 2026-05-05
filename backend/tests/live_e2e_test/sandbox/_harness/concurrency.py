"""Async fan-out helpers for concurrent live-suite tests."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import TypeVar


T = TypeVar("T")


async def gather_with_barrier(
    factories: Sequence[Callable[[], Awaitable[T]]],
) -> list[T]:
    """Start every coroutine simultaneously after a shared barrier.

    Each entry in *factories* is a zero-arg callable that returns the
    coroutine; this lets callers seed per-task closures before fan-out.
    """
    if not factories:
        return []
    barrier = asyncio.Event()

    async def runner(make: Callable[[], Awaitable[T]]) -> T:
        await barrier.wait()
        return await make()

    tasks = [asyncio.create_task(runner(factory)) for factory in factories]
    await asyncio.sleep(0)  # let every task park on the barrier
    barrier.set()
    return await asyncio.gather(*tasks)


__all__ = ["gather_with_barrier"]
