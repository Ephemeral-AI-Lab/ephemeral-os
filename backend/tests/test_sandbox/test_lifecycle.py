"""Tests for sandbox.lifecycle."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock


class TestCloseClient:
    def test_does_nothing_when_client_is_none(self):
        from sandbox.lifecycle import close_client

        close_client(None)

    def test_calls_close_method(self):
        from sandbox.lifecycle import close_client

        async def fake_close():
            pass

        close_mock = MagicMock()
        close_mock.close = MagicMock(return_value=fake_close())

        close_client(close_mock)

        close_mock.close.assert_called_once()

    def test_handles_missing_close_method(self):
        from sandbox.lifecycle import close_client

        client = MagicMock(spec=[])
        close_client(client)


class TestShutdownCachedClient:
    def test_clears_cached_state(self):
        import asyncio
        import sandbox.lifecycle as mod

        async def fake_close():
            pass

        mock_client = MagicMock()
        mock_client.close = MagicMock(return_value=fake_close())
        mod._cached_client = mock_client
        mod._cached_client_key = ("key", "url", "target")
        mod._cached_loop_id = 42

        mod.shutdown_cached_client()

        assert mod._cached_client is None
        assert mod._cached_client_key is None
        assert mod._cached_loop_id is None
