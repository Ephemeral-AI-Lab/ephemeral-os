"""Tests for sandbox.async_client."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, AsyncMock


class TestGetAsyncSandbox:
    @pytest.mark.anyio
    async def test_fetch_sandbox(self, monkeypatch):
        monkeypatch.setenv("DAYTONA_API_KEY", "async-key")
        monkeypatch.setenv("DAYTONA_API_URL", "https://async-url")

        mock_client = MagicMock()
        mock_sandbox = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_sandbox)

        import sandbox.async_client as mod

        mod._cached_client = mock_client
        mod._cached_client_key = ("async-key", "https://async-url", "")
        mod._cached_loop_id = id(pytest.importorskip("asyncio").get_running_loop())

        result = await mod.get_async_sandbox("sb-async-123")

        assert result == mock_sandbox
        mock_client.get.assert_awaited_once_with("sb-async-123")

    @pytest.mark.anyio
    async def test_fetch_sandbox_not_found(self, monkeypatch):
        monkeypatch.setenv("DAYTONA_API_KEY", "async-key")
        monkeypatch.setenv("DAYTONA_API_URL", "https://async-url")

        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=None)

        import sandbox.async_client as mod

        mod._cached_client = mock_client
        mod._cached_client_key = ("async-key", "https://async-url", "")
        mod._cached_loop_id = id(pytest.importorskip("asyncio").get_running_loop())

        with pytest.raises(ValueError, match="not found"):
            await mod.get_async_sandbox("sb-nonexistent")
