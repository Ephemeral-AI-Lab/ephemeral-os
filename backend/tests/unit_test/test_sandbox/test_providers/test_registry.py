"""Tests for the sandbox provider adapter registry."""

from __future__ import annotations

import pytest


class FakeAdapter:
    name = "fake"

    async def exec(self, sandbox_id: str, command: str, **kwargs):  # pragma: no cover
        del sandbox_id, command, kwargs


def test_register_get_dispose_round_trip() -> None:
    from sandbox.provider.registry import (
        dispose_adapter,
        get_adapter,
        register_adapter,
    )

    sandbox_id = "test-registry-round-trip"
    adapter = FakeAdapter()
    dispose_adapter(sandbox_id)

    register_adapter(sandbox_id, adapter)

    assert get_adapter(sandbox_id) is adapter
    dispose_adapter(sandbox_id)
    with pytest.raises(KeyError):
        get_adapter(sandbox_id)


def test_get_unknown_adapter_raises_key_error() -> None:
    from sandbox.provider.registry import dispose_adapter, get_adapter

    sandbox_id = "test-registry-unknown"
    dispose_adapter(sandbox_id)

    with pytest.raises(KeyError):
        get_adapter(sandbox_id)


def test_dispose_adapter_is_idempotent() -> None:
    from sandbox.provider.registry import dispose_adapter

    dispose_adapter("test-registry-idempotent")
    dispose_adapter("test-registry-idempotent")


def test_register_rejects_empty_sandbox_id() -> None:
    from sandbox.provider.registry import register_adapter

    with pytest.raises(ValueError, match="sandbox_id is required"):
        register_adapter("", FakeAdapter())
