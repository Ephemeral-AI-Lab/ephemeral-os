"""Regression tests for Daytona sync/async client cache isolation."""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

import pytest

from sandbox.provider.daytona.client.credentials import client_cache_key


class _DaytonaConfig:
    def __init__(self, **kwargs: str) -> None:
        self.kwargs = kwargs


class _SyncDaytona:
    def __init__(self, config: _DaytonaConfig) -> None:
        self.config = config


class _AsyncDaytona:
    def __init__(self, config: _DaytonaConfig) -> None:
        self.config = config


@pytest.mark.asyncio
async def test_sync_and_async_clients_use_factory_isolated_cache_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_sdk = SimpleNamespace(
        Daytona=_SyncDaytona,
        AsyncDaytona=_AsyncDaytona,
        DaytonaConfig=_DaytonaConfig,
    )
    monkeypatch.setitem(sys.modules, "daytona_sdk", fake_sdk)
    monkeypatch.setenv("DAYTONA_API_KEY", "secret-key")
    monkeypatch.setenv("DAYTONA_API_URL", "https://daytona.example")
    monkeypatch.setenv("DAYTONA_TARGET", "target-a")
    monkeypatch.setattr(
        "sandbox.provider.daytona.client.credentials._load_dotenv_values",
        lambda: {},
    )

    import sandbox.provider.daytona.client.async_client as async_mod
    import sandbox.provider.daytona.client.sync_client as sync_mod

    loop = asyncio.get_running_loop()
    with sync_mod._client_lock:
        sync_mod._cached_client = None
        sync_mod._cached_client_key = None
    with async_mod._client_lock:
        async_mod._cached_clients.clear()

    try:
        sync_client = sync_mod.acquire_client()
        async_client = async_mod.get_async_daytona_client()

        assert isinstance(sync_client, _SyncDaytona)
        assert isinstance(async_client, _AsyncDaytona)
        assert sync_client is not async_client

        sync_key = sync_mod._cached_client_key
        async_key, cached_async_client = async_mod._cached_clients[loop]
        assert cached_async_client is async_client
        assert sync_key == client_cache_key(
            "Daytona",
            api_key="secret-key",
            api_url="https://daytona.example",
            target="target-a",
        )
        assert async_key == client_cache_key(
            "AsyncDaytona",
            api_key="secret-key",
            api_url="https://daytona.example",
            target="target-a",
        )
        assert sync_key is not None
        assert sync_key[0] == "Daytona"
        assert async_key[0] == "AsyncDaytona"
        assert "secret-key" not in sync_key
        assert "secret-key" not in async_key
    finally:
        with sync_mod._client_lock:
            sync_mod._cached_client = None
            sync_mod._cached_client_key = None
        with async_mod._client_lock:
            async_mod._cached_clients.clear()
