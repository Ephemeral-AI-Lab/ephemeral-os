"""Async client lifecycle cleanup helpers."""

from __future__ import annotations

import asyncio
import inspect
import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)


def close_client(client: Any) -> None:
    if client is None:
        return
    close_fn = getattr(client, "close", None)
    if not callable(close_fn):
        return
    try:
        close_result = close_fn()
    except Exception:
        logger.debug("Failed to close cached AsyncDaytona client", exc_info=True)
        return
    if not inspect.isawaitable(close_result):
        return

    def _run_close() -> None:
        close_loop: asyncio.AbstractEventLoop | None = None
        try:
            close_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(close_loop)
            close_loop.run_until_complete(close_result)
        except Exception:
            logger.debug("Failed to await AsyncDaytona close", exc_info=True)
        finally:
            if close_loop is not None:
                close_loop.close()

    closer = threading.Thread(target=_run_close, name="daytona-async-client-close", daemon=True)
    closer.start()
    closer.join(timeout=1.0)


async def async_close_client(client: Any) -> None:
    """Close an async Daytona client on the currently running event loop."""
    if client is None:
        return
    close_fn = getattr(client, "close", None)
    if not callable(close_fn):
        return
    try:
        close_result = close_fn()
        if inspect.isawaitable(close_result):
            await close_result
    except Exception:
        logger.debug("Failed to close cached AsyncDaytona client", exc_info=True)


async def shutdown_cached_client_async() -> None:
    """Close cached AsyncDaytona clients owned by the active event loop.

    This is the preferred cleanup path for scripts and async tests. It closes
    the client before ``asyncio.run`` tears down the loop that owns aiohttp's
    underlying sessions, avoiding unclosed-session warnings at interpreter exit.
    """
    from sandbox.providers.daytona.client import async_ as async_client_mod

    running_loop = asyncio.get_running_loop()
    active_loop_clients: list[Any] = []
    fallback_clients: list[Any] = []
    with async_client_mod._client_lock:
        for loop, (_, client) in list(async_client_mod._cached_clients.items()):
            del async_client_mod._cached_clients[loop]
            if loop is running_loop:
                active_loop_clients.append(client)
            else:
                fallback_clients.append(client)
    for client in active_loop_clients:
        await async_close_client(client)
    for client in fallback_clients:
        close_client(client)


__all__ = [
    "async_close_client",
    "close_client",
    "shutdown_cached_client_async",
]
