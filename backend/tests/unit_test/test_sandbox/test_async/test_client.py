"""Tests for sandbox.async_client."""

from __future__ import annotations

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock

from sandbox.provider.daytona.client import client_cache_key


class TestGetAsyncSandbox:
    @pytest.mark.anyio
    async def test_fetch_sandbox(self, monkeypatch):
        monkeypatch.setenv("DAYTONA_API_KEY", "async-key")
        monkeypatch.setenv("DAYTONA_API_URL", "https://async-url")

        mock_client = MagicMock()
        mock_sandbox = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_sandbox)

        import sandbox.provider.daytona.client as mod

        loop = asyncio.get_running_loop()
        with mod._async_client_lock:
            mod._cached_clients.clear()
            mod._cached_clients[loop] = (
                client_cache_key(
                    "AsyncDaytona",
                    api_key="async-key",
                    api_url="https://async-url",
                    target="",
                ),
                mock_client,
            )

        result = await mod.get_async_sandbox("sb-async-123")

        assert result == mock_sandbox
        # Theme 5 CR-02: client.get must carry the sandbox-fetch timeout to
        # bound the scheduler-degraded hang failure mode.
        mock_client.get.assert_awaited_once()
        call_args = mock_client.get.await_args
        assert call_args.args == ("sb-async-123",)
        assert call_args.kwargs.get("timeout") is not None

    @pytest.mark.anyio
    async def test_fetch_sandbox_not_found(self, monkeypatch):
        monkeypatch.setenv("DAYTONA_API_KEY", "async-key")
        monkeypatch.setenv("DAYTONA_API_URL", "https://async-url")

        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=None)

        import sandbox.provider.daytona.client as mod

        loop = asyncio.get_running_loop()
        with mod._async_client_lock:
            mod._cached_clients.clear()
            mod._cached_clients[loop] = (
                client_cache_key(
                    "AsyncDaytona",
                    api_key="async-key",
                    api_url="https://async-url",
                    target="",
                ),
                mock_client,
            )

        with pytest.raises(ValueError, match="not found"):
            await mod.get_async_sandbox("sb-nonexistent")
