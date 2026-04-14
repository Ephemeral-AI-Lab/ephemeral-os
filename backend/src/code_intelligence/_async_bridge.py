"""Sync bridge for possibly-awaitable sandbox results.

Several CI subsystems call SDK methods that return either sync values
(real local code) or coroutines (the Daytona async client). They all
need the same "run this synchronously" shim, which used to be copy-
pasted into four files.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
from typing import Any


def run_sync(result: Any) -> Any:
    """Resolve *result* synchronously if it is awaitable, else return it."""
    if not inspect.isawaitable(result):
        return result
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, result).result()
    return asyncio.run(result)
