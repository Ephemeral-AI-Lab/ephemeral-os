"""Tests for the daytona client shutdown helpers (post-lifecycle migration)."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from sandbox.provider.daytona.client import client_cache_key


class TestCloseClient:
    def test_does_nothing_when_client_is_none(self):
        from sandbox.provider.daytona.client import close_client

        close_client(None)

    def test_calls_close_method(self):
        from sandbox.provider.daytona.client import close_client

        async def fake_close():
            pass

        close_mock = MagicMock()
        close_mock.close = MagicMock(return_value=fake_close())

        close_client(close_mock)

        close_mock.close.assert_called_once()

    def test_handles_missing_close_method(self):
        from sandbox.provider.daytona.client import close_client

        client = MagicMock(spec=[])
        close_client(client)


class TestAsyncCloseClient:
    def test_awaits_close_method(self):
        from sandbox.provider.daytona.client import async_close_client

        closed = False

        class Client:
            async def close(self):
                nonlocal closed
                closed = True

        asyncio.run(async_close_client(Client()))

        assert closed is True

    def test_handles_missing_close_method(self):
        from sandbox.provider.daytona.client import async_close_client

        client = MagicMock(spec=[])
        asyncio.run(async_close_client(client))


class TestShutdownCachedClient:
    def test_async_shutdown_closes_fallback_loop_clients(self):
        import sandbox.provider.daytona.client as mod

        async def fake_close():
            pass

        mock_client = MagicMock()
        mock_client.close = MagicMock(return_value=fake_close())
        loop = asyncio.new_event_loop()
        mod._cached_clients[loop] = (
            client_cache_key(
                "AsyncDaytona",
                api_key="key",
                api_url="url",
                target="target",
            ),
            mock_client,
        )

        try:
            asyncio.run(mod.shutdown_cached_client_async())
        finally:
            loop.close()

        assert len(mod._cached_clients) == 0

    def test_async_shutdown_joins_fallback_loop_closers_once(self, monkeypatch):
        import sandbox.provider.daytona.client as mod

        client_a = object()
        client_b = object()
        loop_a = asyncio.new_event_loop()
        loop_b = asyncio.new_event_loop()
        started: list[object] = []
        joined: list[tuple[list[object], float]] = []

        def fake_start(client: object) -> object:
            started.append(client)
            return f"closer-{len(started)}"

        def fake_join(closers: list[object], *, timeout: float) -> None:
            joined.append((closers, timeout))

        with mod._async_client_lock:
            mod._cached_clients.clear()
            mod._cached_clients[loop_a] = (
                client_cache_key(
                    "AsyncDaytona",
                    api_key="key-a",
                    api_url="url",
                    target="target",
                ),
                client_a,
            )
            mod._cached_clients[loop_b] = (
                client_cache_key(
                    "AsyncDaytona",
                    api_key="key-b",
                    api_url="url",
                    target="target",
                ),
                client_b,
            )

        monkeypatch.setattr(mod, "_start_async_close_thread", fake_start)
        monkeypatch.setattr(mod, "_join_close_threads", fake_join)
        try:
            asyncio.run(mod.shutdown_cached_client_async())
        finally:
            loop_a.close()
            loop_b.close()

        assert started == [client_a, client_b]
        assert joined == [(["closer-1", "closer-2"], 5.0)]
        assert len(mod._cached_clients) == 0

    def test_async_shutdown_closes_active_loop_client(self):
        import sandbox.provider.daytona.client as mod

        closed = False

        class Client:
            async def close(self):
                nonlocal closed
                closed = True

        async def run() -> None:
            loop = asyncio.get_running_loop()
            with mod._async_client_lock:
                mod._cached_clients.clear()
                mod._cached_clients[loop] = (
                    client_cache_key(
                        "AsyncDaytona",
                        api_key="key",
                        api_url="url",
                        target="target",
                    ),
                    Client(),
                )
            await mod.shutdown_cached_client_async()

        asyncio.run(run())

        assert closed is True
        assert len(mod._cached_clients) == 0
