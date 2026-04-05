"""Async Daytona SDK client wrapper — async initialization and caching.

This module provides async access to Daytona sandboxes using AsyncDaytona,
which returns AsyncSandbox objects with truly async methods (AsyncProcess,
AsyncFileSystem, etc.) that can be properly cancelled via asyncio.CancelledError.
"""

from __future__ import annotations

import atexit
import asyncio
import logging
import os
import inspect
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from daytona_sdk import DaytonaConfig

logger = logging.getLogger(__name__)

_client_lock = threading.Lock()
_cached_client: Any | None = None
_cached_client_key: tuple[str, str, str] | None = None
_cached_loop_id: int | None = None


class AsyncDaytonaUnavailableError(RuntimeError):
    """Raised when Async Daytona SDK is not installed or not configured."""


def _close_async_client(client: Any) -> None:
    """Best-effort close for cached async clients."""
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


def _cleanup_cached_async_daytona_client() -> None:
    """Cleanup hook for interpreter shutdown."""
    global _cached_client, _cached_client_key, _cached_loop_id
    with _client_lock:
        client = _cached_client
        _cached_client = None
        _cached_client_key = None
        _cached_loop_id = None
    _close_async_client(client)


atexit.register(_cleanup_cached_async_daytona_client)


def _require_settings() -> tuple[str, str, str]:
    """Return (api_key, api_url, target) from env vars."""
    api_key = os.environ.get("DAYTONA_API_KEY", "").strip()
    api_url = os.environ.get("DAYTONA_API_URL", "").strip()
    target = os.environ.get("DAYTONA_TARGET", "").strip()

    if not api_key or not api_url:
        raise AsyncDaytonaUnavailableError(
            "Async Daytona is not configured. Set DAYTONA_API_KEY and DAYTONA_API_URL env vars."
        )
    return api_key, api_url, target


def _get_daytona_config() -> "DaytonaConfig":
    """Build DaytonaConfig from settings."""
    api_key, api_url, target = _require_settings()

    try:
        from daytona_sdk import DaytonaConfig
    except ImportError as exc:
        raise AsyncDaytonaUnavailableError(
            "Daytona SDK is not installed. Run: pip install daytona-sdk"
        ) from exc

    cfg_kwargs: dict[str, str] = {"api_key": api_key, "api_url": api_url}
    if target:
        cfg_kwargs["target"] = target
    return DaytonaConfig(**cfg_kwargs)


def get_async_daytona_client() -> Any:
    """Return a cached AsyncDaytona client, creating one if config changed."""
    global _cached_client, _cached_client_key, _cached_loop_id
    loop = asyncio.get_running_loop()
    loop_id = id(loop)

    api_key, api_url, target = _require_settings()
    current_key = (api_key, api_url, target)

    with _client_lock:
        if (
            _cached_client is not None
            and _cached_client_key == current_key
            and _cached_loop_id == loop_id
            and not loop.is_closed()
        ):
            return _cached_client

        if _cached_client is not None and _cached_loop_id != loop_id:
            old_client = _cached_client
            _cached_client = None
            _cached_client_key = None
            _cached_loop_id = None
            _close_async_client(old_client)

        try:
            from daytona_sdk import AsyncDaytona
        except ImportError as exc:
            raise AsyncDaytonaUnavailableError(
                "Async Daytona SDK is not available. Run: pip install daytona-sdk"
            ) from exc

        cfg = _get_daytona_config()
        _cached_client = AsyncDaytona(cfg)
        _cached_client_key = current_key
        _cached_loop_id = loop_id
        logger.info("AsyncDaytona client created (api_url=%s)", api_url)
        return _cached_client


async def get_async_sandbox(sandbox_id: str) -> Any:
    """Fetch and start a pre-created sandbox by ID using async client.

    Returns an AsyncSandbox with async process, fs, and git interfaces
    that support proper cancellation via asyncio.CancelledError.
    """
    client = get_async_daytona_client()
    sandbox = await client.get(sandbox_id)
    if sandbox is None:
        raise ValueError(f"Sandbox '{sandbox_id}' not found")
    return sandbox
