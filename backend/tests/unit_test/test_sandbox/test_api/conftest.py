"""Shared fixtures for sandbox API tests."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import pytest

TransportHandler = Callable[
    [str, str, dict[str, object], int],
    Awaitable[dict[str, Any]],
]


class RecordingTransport:
    def __init__(self, handler: TransportHandler) -> None:
        self._handler = handler
        self.calls: list[tuple[str, str, dict[str, object], int]] = []

    async def call(
        self,
        sandbox_id: str,
        op: str,
        payload: Mapping[str, object],
        *,
        timeout: int,
    ) -> dict[str, Any]:
        payload_dict = dict(payload)
        self.calls.append((sandbox_id, op, payload_dict, timeout))
        return await self._handler(sandbox_id, op, payload_dict, timeout)


@pytest.fixture
def recording_transport_factory():
    return RecordingTransport
