"""Tests for sandbox.lifecycle."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest


class TestLifecycleProviderFactory:
    def test_defaults_to_daytona_lifecycle_provider(self):
        from sandbox.lifecycle.factory import lifecycle_provider_for
        from sandbox.providers.daytona.lifecycle import DaytonaSandboxLifecycle

        assert isinstance(lifecycle_provider_for(), DaytonaSandboxLifecycle)

    def test_rejects_unknown_provider(self):
        from sandbox.lifecycle.factory import lifecycle_provider_for

        with pytest.raises(ValueError, match="Unsupported sandbox provider"):
            lifecycle_provider_for(provider="unknown")


class TestCloseClient:
    def test_does_nothing_when_client_is_none(self):
        from sandbox.providers.daytona.client.async_shutdown import close_client

        close_client(None)

    def test_calls_close_method(self):
        from sandbox.providers.daytona.client.async_shutdown import close_client

        async def fake_close():
            pass

        close_mock = MagicMock()
        close_mock.close = MagicMock(return_value=fake_close())

        close_client(close_mock)

        close_mock.close.assert_called_once()

    def test_handles_missing_close_method(self):
        from sandbox.providers.daytona.client.async_shutdown import close_client

        client = MagicMock(spec=[])
        close_client(client)


class TestAsyncCloseClient:
    def test_awaits_close_method(self):
        from sandbox.providers.daytona.client.async_shutdown import async_close_client

        closed = False

        class Client:
            async def close(self):
                nonlocal closed
                closed = True

        asyncio.run(async_close_client(Client()))

        assert closed is True

    def test_handles_missing_close_method(self):
        from sandbox.providers.daytona.client.async_shutdown import async_close_client

        client = MagicMock(spec=[])
        asyncio.run(async_close_client(client))


class TestShutdownCachedClient:
    def test_clears_async_client_cached_state(self):
        import sandbox.providers.daytona.client.async_ as async_client_mod
        import sandbox.providers.daytona.client.async_shutdown as mod

        async def fake_close():
            pass

        mock_client = MagicMock()
        mock_client.close = MagicMock(return_value=fake_close())
        loop = asyncio.new_event_loop()
        async_client_mod._cached_clients[loop] = (("key", "url", "target"), mock_client)

        try:
            mod.shutdown_cached_client()
        finally:
            loop.close()

        assert len(async_client_mod._cached_clients) == 0

    def test_async_shutdown_closes_active_loop_client(self):
        import sandbox.providers.daytona.client.async_ as async_client_mod
        import sandbox.providers.daytona.client.async_shutdown as mod

        closed = False

        class Client:
            async def close(self):
                nonlocal closed
                closed = True

        async def run() -> None:
            loop = asyncio.get_running_loop()
            with async_client_mod._client_lock:
                async_client_mod._cached_clients.clear()
                async_client_mod._cached_clients[loop] = (("key", "url", "target"), Client())
            await mod.shutdown_cached_client_async()

        asyncio.run(run())

        assert closed is True
        assert len(async_client_mod._cached_clients) == 0
