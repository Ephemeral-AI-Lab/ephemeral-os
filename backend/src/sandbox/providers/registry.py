"""Process-local sandbox provider adapter registry."""

from __future__ import annotations

import threading

from sandbox.providers.protocol import ProviderAdapter

_ADAPTERS: dict[str, ProviderAdapter] = {}
_LOCK = threading.Lock()


def register_adapter(sandbox_id: str, adapter: ProviderAdapter) -> None:
    """Bind *sandbox_id* to *adapter* in this orchestrator process."""
    if not sandbox_id:
        raise ValueError("sandbox_id is required")
    with _LOCK:
        _ADAPTERS[sandbox_id] = adapter


def get_adapter(sandbox_id: str) -> ProviderAdapter:
    """Return the provider adapter for *sandbox_id*.

    Raises ``KeyError`` when no adapter has been registered.
    """
    with _LOCK:
        return _ADAPTERS[sandbox_id]


def dispose_adapter(sandbox_id: str) -> None:
    """Remove the provider adapter for *sandbox_id* if present."""
    with _LOCK:
        _ADAPTERS.pop(sandbox_id, None)


__all__ = ["dispose_adapter", "get_adapter", "register_adapter"]
