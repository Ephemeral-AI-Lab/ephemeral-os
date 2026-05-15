"""Regression tests for Daytona sync/async client cache isolation."""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

import pytest

from sandbox.provider.daytona.client import (
    DaytonaUnavailableError,
    build_sdk_client,
    client_cache_key,
)


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
        "sandbox.provider.daytona.client._load_dotenv_values",
        lambda: {},
    )

    import sandbox.provider.daytona.client as client_mod

    loop = asyncio.get_running_loop()
    with client_mod._sync_client_lock:
        client_mod._cached_client = None
        client_mod._cached_client_key = None
    with client_mod._async_client_lock:
        client_mod._cached_clients.clear()

    try:
        sync_client = client_mod.acquire_client()
        async_client = client_mod.get_async_daytona_client()

        assert isinstance(sync_client, _SyncDaytona)
        assert isinstance(async_client, _AsyncDaytona)
        assert sync_client is not async_client

        sync_key = client_mod._cached_client_key
        async_key, cached_async_client = client_mod._cached_clients[loop]
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
        with client_mod._sync_client_lock:
            client_mod._cached_client = None
            client_mod._cached_client_key = None
        with client_mod._async_client_lock:
            client_mod._cached_clients.clear()


def test_client_cache_key_rejects_unknown_factory() -> None:
    with pytest.raises(ValueError, match="unsupported Daytona SDK factory"):
        client_cache_key(  # type: ignore[arg-type]
            "OtherDaytona",
            api_key="secret-key",
            api_url="https://daytona.example",
            target="target-a",
        )


def test_build_sdk_client_rejects_unknown_factory_before_import() -> None:
    with pytest.raises(ValueError, match="unsupported Daytona SDK factory"):
        build_sdk_client(  # type: ignore[arg-type]
            "OtherDaytona",
            api_key="secret-key",
            api_url="https://daytona.example",
            target="target-a",
            unavailable_cls=DaytonaUnavailableError,
            not_installed_message="not installed",
        )
